"""Gate Kiosk — continuous face-scan auto-punch.

Runs on an iPad/tablet at the warehouse gate. The guard signs in once at
shift start (role=GateGuard). The page then keeps the camera live; whenever
a face is presented, we identify the worker against every enrolled
FaceTemplate's stored references (5 poses each by default) and create a
Pending Attendance row routed to the dual-approval queue.

Decisions:
- A face is accepted when `min_distance <= FACE_MATCH_THRESHOLD` against any
  reference of any worker (closest match wins). The closest worker is the
  identified person.
- A second, slightly looser threshold (`KIOSK_CONFIDENCE_FLOOR`) flags
  "low confidence" — we still create the punch but mark it as needing
  extra HR scrutiny so warehouse ops aren't blocked.
- The same worker re-detected within `KIOSK_REDETECT_SECONDS` is suppressed
  so a worker standing in front of the camera doesn't get punched twice.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from models import (db, Worker, FaceTemplate, Project, Attendance,
                   AttendanceApproval, ROLE_GATE_GUARD, ROLE_ADMIN,
                   ATT_PRESENT)
from utils import role_required, descriptor_loads, best_face_match

bp = Blueprint("kiosk", __name__)


@bp.route("/kiosk")
@login_required
@role_required(ROLE_GATE_GUARD, ROLE_ADMIN)
def gate_screen():
    """The kiosk full-screen page. Guard signs in here, then the camera runs."""
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    return render_template("kiosk/gate.html", projects=projects)


def _confidence_pct(distance: float) -> int:
    return max(0, int(round((1.0 - distance) * 100)))


@bp.route("/kiosk/identify", methods=["POST"])
@login_required
@role_required(ROLE_GATE_GUARD, ROLE_ADMIN)
def identify():
    """Body: {descriptor: [128 floats], project_id, kind: 'in'|'out'}
       Searches every enrolled worker, picks the closest match, and (if it
       passes the threshold) creates / closes a Pending attendance row.
       Returns identification result for the kiosk UI to display."""
    d = request.get_json() or {}
    given = d.get("descriptor") or []
    if isinstance(given, str):
        given = descriptor_loads(given)
    if not given or len(given) != 128:
        return jsonify(ok=False, reason="bad_descriptor",
                       message="No usable face captured. Step closer."), 400

    threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.45))
    # Wider net — we still record the punch but flag it.
    confidence_floor = float(current_app.config.get("KIOSK_CONFIDENCE_FLOOR", 0.55))
    redetect_seconds = int(current_app.config.get("KIOSK_REDETECT_SECONDS", 30))

    project_id = int(d.get("project_id") or 0)
    if not project_id:
        p = Project.query.filter_by(is_active=True).first()
        project_id = p.id if p else 0
    if not project_id:
        return jsonify(ok=False, reason="no_project",
                       message="No active project configured. Ask admin."), 400
    kind = (d.get("kind") or "in").lower()
    if kind not in ("in", "out"):
        kind = "in"

    # Search every enrolled worker, take the global minimum distance.
    best_worker_id = None
    best_dist = 99.0
    for tpl in FaceTemplate.query.all():
        refs = tpl.references()
        if not refs:
            continue
        d_min, _ = best_face_match(given, refs)
        if d_min < best_dist:
            best_dist = d_min
            best_worker_id = tpl.worker_id

    if best_worker_id is None:
        return jsonify(ok=False, reason="no_enrolled_workers",
                       message="No workers enrolled yet."), 404

    worker = db.session.get(Worker, best_worker_id)
    confidence = _confidence_pct(best_dist)

    # Below the floor? Treat as unknown and refuse the punch entirely.
    if best_dist > confidence_floor:
        return jsonify(ok=False, reason="unknown_face",
                       distance=round(best_dist, 4), similarity_pct=confidence,
                       message="Face not recognised. Please re-enrol or talk to HR."), 403

    # Has this same worker been seen at the kiosk in the last N seconds?
    # Suppress so they don't get punched twice while standing in front of the camera.
    cutoff = datetime.utcnow() - timedelta(seconds=redetect_seconds)
    recent = (Attendance.query
              .filter(Attendance.worker_id == worker.id,
                      Attendance.captured_at >= cutoff)
              .order_by(Attendance.captured_at.desc())
              .first())
    if recent:
        return jsonify(ok=True, suppressed=True,
                       worker={"code": worker.code, "name": worker.full_name,
                               "category": worker.category},
                       similarity_pct=confidence,
                       message=f"{worker.full_name} just punched — waiting…")

    # Find or create today's attendance row for this worker × project.
    today = date.today()
    now = datetime.utcnow()
    att = Attendance.query.filter_by(
        worker_id=worker.id, project_id=project_id, work_date=today).first()
    is_new = False
    if not att:
        # First detection of the day → IN punch.
        actual_kind = "in" if kind == "in" else "out"  # honour the toggle
        att = Attendance(worker_id=worker.id, project_id=project_id,
                         work_date=today, status=ATT_PRESENT, source="Worker",
                         hours=Decimal("8.00"))
        if actual_kind == "in":
            att.punch_in_at = now
        else:
            # Guard set OUT before any IN — rare, but record so HR can fix.
            att.punch_out_at = now
        db.session.add(att); db.session.flush()
        is_new = True
    else:
        # Row exists — kind determines which timestamp to set.
        if kind == "in" and not att.punch_in_at:
            att.punch_in_at = now
        elif kind == "out":
            att.punch_out_at = now
            # Auto-compute hours from the IN→OUT gap (cap 12h, floor 0)
            if att.punch_in_at:
                gap = (now - att.punch_in_at).total_seconds() / 3600.0
                if 0 < gap <= 12:
                    att.hours = Decimal(str(round(gap, 2)))

    # captured_at tracks the LAST kiosk action — used for the redetect-suppress
    att.captured_at = now
    # Stash kind + guard in source for the audit trail
    att.source = f"Kiosk:{kind}:guard={current_user.username}"

    # Make sure the right approval rows exist for THIS worker's agency mode.
    # - none    (PROCAM)   → punch IS the record, no approval row created
    # - hr_only (legacy)   → only HR/Client side
    # - dual    (NPR/vendor) → Vendor + Client both must sign off
    sides_now = {a.side for a in att.approvals}
    mode = worker.agency.approver_mode if worker.agency else "none"
    if mode == "dual" and "Vendor" not in sides_now:
        db.session.add(AttendanceApproval(attendance_id=att.id, side="Vendor",
                                          status="Pending", decided_by_id=current_user.id))
    if mode in ("hr_only", "dual") and "Client" not in sides_now:
        db.session.add(AttendanceApproval(attendance_id=att.id, side="Client",
                                          status="Pending", decided_by_id=current_user.id))
    db.session.commit()

    flagged = best_dist > threshold  # passed floor but below strict threshold
    # Tail message depends on whether approval is needed
    if mode == "none":
        tail = "Recorded — no approval needed."
    elif mode == "dual":
        tail = "Pending Contractor + Procam Rep approval."
    else:
        tail = "Pending HR approval."
    return jsonify(
        ok=True, kind=kind, new_row=is_new, flagged_for_review=flagged,
        worker={"code": worker.code, "name": worker.full_name,
                "category": worker.category,
                "agency": worker.agency.name if worker.agency else None,
                "skill": worker.skill.name if worker.skill else None},
        similarity_pct=confidence, distance=round(best_dist, 4),
        attendance_id=att.id, project_id=project_id,
        message=(f"{'Welcome' if kind == 'in' else 'Goodbye'}, {worker.full_name} "
                 f"— {confidence}% match. {'⚠ flagged for review' if flagged else ''} {tail}")
    )


@bp.route("/kiosk/recent")
@login_required
@role_required(ROLE_GATE_GUARD, ROLE_ADMIN)
def recent_punches():
    """Last 20 kiosk-captured punches today — for the live feed on the kiosk."""
    today = date.today()
    rows = (Attendance.query
            .filter(Attendance.work_date == today,
                    Attendance.source.like("Kiosk%"))
            .order_by(Attendance.captured_at.desc())
            .limit(20).all())
    return jsonify(rows=[{
        "code": r.worker.code if r.worker else "?",
        "name": r.worker.full_name if r.worker else "?",
        "at": r.captured_at.replace(tzinfo=timezone.utc)
                          .astimezone(timezone(timedelta(hours=5, minutes=30)))
                          .strftime("%H:%M:%S") if r.captured_at else "",
        "source": r.source or "",
    } for r in rows])
