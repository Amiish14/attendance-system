"""Worker punch & regularization."""
from datetime import date, datetime
from decimal import Decimal
import json

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user

from models import (
    db, Worker, Project, Attendance, AttendanceApproval, FaceTemplate,
    Regularization, ATT_PRESENT, ATT_HALF,
    ROLE_WORKER, ROLE_VENDOR_REP, ROLE_PROCAM_REP, ROLE_ADMIN,
)
from utils import role_required, parse_date, face_distance, best_face_match, descriptor_loads

bp = Blueprint("attendance", __name__)


# ---------------------------------------------------------------------------
# Worker home / punch
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def worker_home():
    """If the user is a worker, show punch screen; otherwise redirect."""
    if current_user.role != ROLE_WORKER:
        return redirect(url_for("index"))
    worker = current_user.worker
    today = date.today()
    todays = (Attendance.query
              .filter_by(worker_id=worker.id, work_date=today).first()
              if worker else None)
    recent = (Attendance.query.filter_by(worker_id=worker.id)
              .order_by(Attendance.work_date.desc()).limit(14).all()
              if worker else [])
    return render_template("attendance/worker_home.html",
                           worker=worker, today_att=todays, recent=recent)


@bp.route("/enroll-face", methods=["GET", "POST"])
@login_required
def enroll_face():
    """Self-enrolment for any employee (Admin, ProcamRep, Worker), or admin
       acting on behalf via ?worker_id=. Every employee with a linked Worker
       record can enrol their own face — that's what unlocks gate-kiosk
       identification for them."""
    worker = None
    # 1) If an explicit ?worker_id= was passed, an Admin/Manager is enrolling
    #    on behalf of someone else.
    wid = request.args.get("worker_id")
    if wid:
        worker = Worker.query.get(int(wid))
    # 2) Otherwise, the logged-in user is enrolling THEMSELVES — works for
    #    any role that's linked to a Worker record.
    elif getattr(current_user, "worker_id", None):
        worker = current_user.worker
    if not worker:
        flash("No worker record linked to your account — ask HR to set one up.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        # The form posts a JSON dict {"Centre":[...], "Left":[...], "Right":[...],
        # "Up":[...], "Down":[...]} — the 5-pose enrolment from the front-end.
        # Backward-compat: a flat 128-float array is still accepted.
        import json as _json
        raw = request.form.get("descriptor") or ""
        try:
            data = _json.loads(raw)
        except Exception:
            data = []
        # Normalise to a dict-of-poses + a list-of-vectors for storage
        poses: dict[str, list[float]] = {}
        if isinstance(data, dict):
            # New shape: {"Centre": [...], ...}
            for pose, vec in data.items():
                if isinstance(vec, list) and len(vec) == 128:
                    poses[pose] = [float(x) for x in vec]
        elif isinstance(data, list):
            # Could be a flat 128 vector or a list of vectors
            if data and isinstance(data[0], (int, float)) and len(data) == 128:
                poses["Centre"] = [float(x) for x in data]
            elif data and isinstance(data[0], list):
                for i, vec in enumerate(data):
                    if isinstance(vec, list) and len(vec) == 128:
                        poses[f"Pose{i+1}"] = [float(x) for x in vec]

        if len(poses) == 0:
            flash("Could not capture any face descriptors — try again in good light.", "error")
        elif len(poses) < 3:
            flash(f"Only {len(poses)} pose(s) captured. Please complete at least 3 poses (Centre + 2 angles).", "error")
        else:
            tpl = worker.face_template
            payload = _json.dumps({"poses": poses})
            if not tpl:
                tpl = FaceTemplate(worker_id=worker.id,
                                   descriptor_json=payload, pose_count=len(poses))
                db.session.add(tpl)
            else:
                tpl.descriptor_json = payload
                tpl.pose_count = len(poses)
                tpl.enrolled_at = datetime.utcnow()
            db.session.commit()
            flash(f"Face enrolled with {len(poses)} pose(s): " +
                  ", ".join(poses.keys()) + ".", "success")
            # Send the user to their role's home page after enrolment,
            # not always the worker dashboard.
            return redirect(url_for("index"))

    poses_required = list(current_app.config.get(
        "ENROLMENT_POSES", ["Centre", "Left", "Right", "Up", "Down"]))
    glasses_poses = list(current_app.config.get(
        "GLASSES_POSES", ["Glasses-On", "Glasses-Off"]))
    return render_template("attendance/enroll_face.html",
                           worker=worker, poses=poses_required,
                           glasses_poses=glasses_poses)


@bp.route("/punch", methods=["POST"])
@login_required
def punch():
    """DISABLED — workers can no longer self-punch from their account.
       Attendance must be captured at the gate kiosk (role=GateGuard).
       This endpoint is kept here only to return a clear refusal."""
    return jsonify(
        ok=False,
        reason="kiosk_only",
        message=("Self-punch is disabled. Attendance is captured at the warehouse "
                 "kiosk only. Walk up to the gate camera; the system will identify "
                 "you and punch you in.")
    ), 403
    # (Unreachable but kept to make the legacy code visible in diffs.)
    if current_user.role != ROLE_WORKER:
        return jsonify(error="only workers can punch"), 403
    worker = current_user.worker
    if not worker:
        return jsonify(error="no worker linked"), 400
    tpl = worker.face_template
    if not tpl:
        return jsonify(error="face not enrolled — see admin/vendor"), 400

    descriptor = request.form.get("descriptor") or "[]"
    given = descriptor_loads(descriptor)
    # Pull every stored reference (multi-pose) and take the closest one.
    refs = tpl.references()
    dist, ref_idx = best_face_match(given, refs)
    threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.45))
    similarity_pct = max(0, int(round((1.0 - dist) * 100)))
    if dist > threshold:
        return jsonify(ok=False, reason="face mismatch",
                       distance=round(dist, 4), threshold=threshold,
                       similarity_pct=similarity_pct,
                       refs_checked=len(refs),
                       message=(f"Face does NOT match {worker.full_name}'s enrolled face — "
                                f"only {similarity_pct}% similar against {len(refs)} stored angles. "
                                f"Need ≥{int((1-threshold)*100)}%.")), 403

    project_id = int(request.form.get("project_id") or 0)
    if not project_id:
        p = Project.query.filter_by(is_active=True).first()
        project_id = p.id if p else 0
    if not project_id:
        return jsonify(error="no project"), 400

    # Accept an explicit kind=in|out; default IN when the row doesn't exist yet,
    # OUT when it does (most natural for re-clicks during the day).
    requested_kind = (request.form.get("kind") or "").lower()
    today = date.today()
    att = Attendance.query.filter_by(
        worker_id=worker.id, project_id=project_id, work_date=today).first()

    now = datetime.utcnow()
    if not att:
        # First punch of the day → IN
        kind = requested_kind if requested_kind in ("in", "out") else "in"
        att = Attendance(worker_id=worker.id, project_id=project_id,
                         work_date=today, status=ATT_PRESENT, source="Worker",
                         hours=Decimal("8.00"), punch_in_at=now)
        db.session.add(att)
        # Both approvals start Pending (worker self-punch still goes through dual-approval)
        db.session.flush()
        db.session.add(AttendanceApproval(attendance_id=att.id, side="Vendor",
                                          status="Pending", decided_by_id=current_user.id))
        db.session.add(AttendanceApproval(attendance_id=att.id, side="Client",
                                          status="Pending", decided_by_id=current_user.id))
    else:
        # Row exists. Default is OUT, unless an explicit kind says otherwise.
        kind = requested_kind if requested_kind in ("in", "out") else "out"
        if kind == "in":
            # Replace / set IN — only allowed if not already set OR HR override
            if not att.punch_in_at:
                att.punch_in_at = now
        else:
            att.punch_out_at = now
            # Update billable hours from the actual gap (cap at 12h)
            if att.punch_in_at:
                gap_hours = (now - att.punch_in_at).total_seconds() / 3600.0
                if 0 < gap_hours <= 12:
                    att.hours = Decimal(str(round(gap_hours, 2)))
    att.captured_at = now
    db.session.commit()
    return jsonify(ok=True, attendance_id=att.id, kind=kind,
                   distance=round(dist, 4), similarity_pct=similarity_pct,
                   punch_in_at=att.punch_in_at.isoformat() if att.punch_in_at else None,
                   punch_out_at=att.punch_out_at.isoformat() if att.punch_out_at else None,
                   message=(f"{'✓ Welcome' if kind == 'in' else '✓ Goodbye'}, "
                            f"{worker.full_name} — {similarity_pct}% match. "
                            f"Pending Contractor + Procam Rep approval."))


# ---------------------------------------------------------------------------
# Regularization
# ---------------------------------------------------------------------------
@bp.route("/regularization/new", methods=["GET", "POST"])
@login_required
def regularization_new():
    projects = Project.query.filter_by(is_active=True).all()
    if request.method == "POST":
        f = request.form
        # if worker, fix worker_id from current_user
        if current_user.role == ROLE_WORKER:
            worker_id = current_user.worker_id
        else:
            worker_id = int(f["worker_id"])
        r = Regularization(
            worker_id=worker_id,
            project_id=int(f["project_id"]),
            work_date=parse_date(f["work_date"]),
            requested_status=f.get("requested_status") or ATT_PRESENT,
            reason=f.get("reason") or "",
            requested_by_id=current_user.id,
        )
        db.session.add(r)
        db.session.commit()
        flash("Regularization request submitted.", "success")
        if current_user.role == ROLE_WORKER:
            return redirect(url_for("attendance.worker_home"))
        return redirect(url_for("attendance.regularization_queue"))

    workers = None
    if current_user.role != ROLE_WORKER:
        workers = Worker.query.filter_by(is_active=True).order_by(Worker.full_name).all()
    return render_template("attendance/regularization_new.html",
                           projects=projects, workers=workers)


@bp.route("/regularization/queue")
@login_required
@role_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def regularization_queue():
    rows = (Regularization.query
            .filter_by(status="Pending")
            .order_by(Regularization.created_at.desc()).all())
    return render_template("attendance/regularization_queue.html", rows=rows)


@bp.route("/regularization/<int:rid>/decide", methods=["POST"])
@login_required
@role_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def regularization_decide(rid):
    r = Regularization.query.get_or_404(rid)
    decision = request.form.get("decision") or "Approved"
    remark = request.form.get("remark") or ""
    r.status = decision
    r.decided_by_id = current_user.id
    r.decided_at = datetime.utcnow()
    r.decision_remark = remark
    if decision == "Approved":
        # create the missing Attendance + both approvals
        att = Attendance.query.filter_by(
            worker_id=r.worker_id, project_id=r.project_id, work_date=r.work_date).first()
        if not att:
            att = Attendance(worker_id=r.worker_id, project_id=r.project_id,
                             work_date=r.work_date, status=r.requested_status,
                             hours=Decimal("8.00") if r.requested_status == ATT_PRESENT
                                   else Decimal("4.00"),
                             source="Regularized")
            db.session.add(att)
            db.session.flush()
        # both approvals approved
        for side in ("Vendor", "Client"):
            ap = att.approval(side) or AttendanceApproval(attendance_id=att.id, side=side)
            ap.status = "Approved"
            ap.decided_by_id = current_user.id
            ap.decided_at = datetime.utcnow()
            ap.remark = f"regularized by {current_user.username}"
            if not ap.id:
                db.session.add(ap)
    db.session.commit()
    flash(f"Regularization {decision}.", "success")
    return redirect(url_for("attendance.regularization_queue"))
