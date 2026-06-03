"""JSON API consumed by the future mobile app. Prefixed at /api/v1."""
from datetime import date, datetime
from decimal import Decimal
import json

from flask import Blueprint, request, jsonify, g, current_app

from models import (
    db, User, Agency, Project, Skill, Worker, RateCard, FaceTemplate,
    Attendance, AttendanceApproval, Regularization, Invoice, InvoiceLine,
    ATT_PRESENT, ATT_HALF, ATT_ABSENT, ATT_REGULARIZED,
    ROLE_ADMIN, ROLE_PROCAM_REP, ROLE_VENDOR_REP, ROLE_WORKER,
)
from utils import (
    jwt_encode, jwt_decode, jwt_required, face_distance, best_face_match,
    descriptor_loads, parse_date,
)
from routes.invoice import build_invoice

bp = Blueprint("api", __name__)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def s_user(u: User):
    return {"id": u.id, "username": u.username, "display_name": u.display_name,
            "role": u.role, "agency_id": u.agency_id, "worker_id": u.worker_id}


def s_agency(a: Agency):
    return {
        "id": a.id, "code": a.code, "name": a.name,
        "contact_person": a.contact_person, "phone": a.phone, "email": a.email,
        "gstin": a.gstin, "pan": a.pan,
        "default_gst_rate": float(a.default_gst_rate or 0),
        "default_tds_rate": float(a.default_tds_rate or 0),
        "is_active": a.is_active,
    }


def s_project(p: Project):
    return {
        "id": p.id, "code": p.code, "name": p.name,
        "client_name": p.client_name, "location": p.location,
        "procam_rep_id": p.procam_rep_id, "is_active": p.is_active,
    }


def s_worker(w: Worker):
    return {
        "id": w.id, "code": w.code, "full_name": w.full_name,
        "agency_id": w.agency_id, "skill_id": w.skill_id,
        "skill": w.skill.name if w.skill else None,
        "category": w.category,
        "mobile": w.mobile, "is_active": w.is_active,
        "has_face": bool(w.face_template),
    }


def s_attendance(a: Attendance):
    v = a.approval("Vendor")
    c = a.approval("Client")
    return {
        "id": a.id, "worker_id": a.worker_id, "project_id": a.project_id,
        "work_date": a.work_date.isoformat(),
        "hours": float(a.hours or 0), "status": a.status, "source": a.source,
        "vendor_status": v.status if v else "Pending",
        "client_status": c.status if c else "Pending",
        "dual_approved": a.is_dual_approved,
    }


def s_invoice(inv: Invoice):
    return {
        "id": inv.id, "invoice_no": inv.invoice_no,
        "agency_id": inv.agency_id, "project_id": inv.project_id,
        "period_start": inv.period_start.isoformat(),
        "period_end": inv.period_end.isoformat(),
        "subtotal": float(inv.subtotal or 0),
        "gst_rate": float(inv.gst_rate or 0),
        "gst_amount": float(inv.gst_amount or 0),
        "tds_rate": float(inv.tds_rate or 0),
        "tds_amount": float(inv.tds_amount or 0),
        "net_payable": float(inv.net_payable or 0),
        "status": inv.status,
        "lines": [
            {"worker_id": ln.worker_id, "skill": ln.skill,
             "days_present": ln.days_present, "half_days": ln.half_days,
             "total_billable_days": float(ln.total_billable_days or 0),
             "daily_rate": float(ln.daily_rate or 0),
             "line_amount": float(ln.line_amount or 0)}
            for ln in inv.lines
        ],
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@bp.route("/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    u = User.query.filter_by(username=username).first()
    if not u or not u.check_password(password) or not u.is_active:
        return jsonify(error="invalid credentials"), 401
    token, exp = jwt_encode(u.id, u.role)
    return jsonify(token=token, role=u.role, user=s_user(u),
                   expires_at=exp.isoformat() + "Z")


@bp.route("/auth/refresh", methods=["POST"])
@jwt_required()
def auth_refresh():
    token, exp = jwt_encode(g.api_user.id, g.api_user.role)
    return jsonify(token=token, expires_at=exp.isoformat() + "Z")


@bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    return jsonify(user=s_user(g.api_user))


# ---------------------------------------------------------------------------
# Agencies
# ---------------------------------------------------------------------------
@bp.route("/agencies", methods=["GET"])
@jwt_required()
def agencies_list():
    return jsonify(agencies=[s_agency(a) for a in Agency.query.all()])


@bp.route("/agencies", methods=["POST"])
@jwt_required(ROLE_ADMIN)
def agencies_create():
    d = request.get_json() or {}
    a = Agency(
        code=d["code"], name=d["name"],
        contact_person=d.get("contact_person"), email=d.get("email"),
        phone=d.get("phone"), address=d.get("address"),
        gstin=d.get("gstin"), pan=d.get("pan"),
        bank_name=d.get("bank_name"), account_no=d.get("account_no"),
        ifsc=d.get("ifsc"),
        default_tds_rate=Decimal(str(d.get("default_tds_rate", "2.00"))),
        default_gst_rate=Decimal(str(d.get("default_gst_rate", "18.00"))),
    )
    db.session.add(a)
    db.session.commit()
    return jsonify(agency=s_agency(a)), 201


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@bp.route("/projects", methods=["GET"])
@jwt_required()
def projects_list():
    return jsonify(projects=[s_project(p) for p in Project.query.all()])


@bp.route("/projects", methods=["POST"])
@jwt_required(ROLE_ADMIN)
def projects_create():
    d = request.get_json() or {}
    p = Project(
        code=d["code"], name=d["name"],
        client_name=d.get("client_name"), location=d.get("location"),
        start_date=parse_date(d.get("start_date")),
        end_date=parse_date(d.get("end_date")),
        procam_rep_id=d.get("procam_rep_id"),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(project=s_project(p)), 201


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
@bp.route("/workers", methods=["GET"])
@jwt_required()
def workers_list():
    q = Worker.query
    agency_id = request.args.get("agency_id", type=int)
    if agency_id:
        q = q.filter_by(agency_id=agency_id)
    project_id = request.args.get("project_id", type=int)
    if project_id:
        from models import ProjectAssignment
        ids = [pa.worker_id for pa in
               ProjectAssignment.query.filter_by(project_id=project_id).all()]
        if ids:
            q = q.filter(Worker.id.in_(ids))
    return jsonify(workers=[s_worker(w) for w in q.all()])


@bp.route("/workers/<int:wid>/enroll-face", methods=["POST"])
@jwt_required(ROLE_ADMIN, ROLE_VENDOR_REP, ROLE_WORKER)
def worker_enroll_face(wid):
    """Multi-pose enrolment. Accepts EITHER:
       * {"poses": {"Centre":[...], "Left":[...], "Right":[...], "Up":[...], "Down":[...]}}
       * {"descriptor": {"Centre":[...], ...}}              (alias)
       * {"descriptor": [128-float vec]}                    (legacy, becomes 1 pose)
       * {"descriptor": [[128-float], [128-float], ...]}    (untagged list of poses)
       Stores all of them and reports back the pose count.
    """
    w = Worker.query.get_or_404(wid)
    d = request.get_json() or {}
    raw = d.get("poses") or d.get("descriptor")
    poses: dict[str, list[float]] = {}
    if isinstance(raw, dict):
        for pose, vec in raw.items():
            if isinstance(vec, list) and len(vec) == 128:
                poses[pose] = [float(x) for x in vec]
    elif isinstance(raw, list):
        if raw and isinstance(raw[0], (int, float)) and len(raw) == 128:
            poses["Centre"] = [float(x) for x in raw]
        elif raw and isinstance(raw[0], list):
            for i, vec in enumerate(raw):
                if isinstance(vec, list) and len(vec) == 128:
                    poses[f"Pose{i+1}"] = [float(x) for x in vec]

    if len(poses) == 0:
        return jsonify(error="no valid 128-D descriptors in payload"), 400

    payload = json.dumps({"poses": poses})
    tpl = w.face_template
    if not tpl:
        tpl = FaceTemplate(worker_id=w.id, descriptor_json=payload, pose_count=len(poses))
        db.session.add(tpl)
    else:
        tpl.descriptor_json = payload
        tpl.pose_count = len(poses)
        tpl.enrolled_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True, worker_id=w.id,
                   pose_count=len(poses), poses=list(poses.keys()))


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
@bp.route("/attendance/punch", methods=["POST"])
@jwt_required(ROLE_WORKER)
def attendance_punch():
    """Body: descriptor (list), lat, lng, project_id, kind (in/out)."""
    user = g.api_user
    worker = user.worker
    if not worker:
        return jsonify(error="no worker linked"), 400
    tpl = worker.face_template
    if not tpl:
        return jsonify(error="face not enrolled"), 400

    d = request.get_json() or {}
    given = d.get("descriptor") or []
    if isinstance(given, str):
        given = descriptor_loads(given)
    refs = tpl.references()
    dist, ref_idx = best_face_match([float(x) for x in given], refs)
    threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.45))
    similarity_pct = max(0, int(round((1.0 - dist) * 100)))
    if dist > threshold:
        return jsonify(ok=False, reason="face mismatch",
                       distance=round(dist, 4), threshold=threshold,
                       similarity_pct=similarity_pct,
                       refs_checked=len(refs)), 403

    project_id = d.get("project_id")
    if not project_id:
        p = Project.query.filter_by(is_active=True).first()
        project_id = p.id if p else None
    if not project_id:
        return jsonify(error="no project"), 400

    today = date.today()
    att = Attendance.query.filter_by(
        worker_id=worker.id, project_id=project_id, work_date=today).first()
    if not att:
        att = Attendance(worker_id=worker.id, project_id=project_id,
                         work_date=today, status=ATT_PRESENT, source="Worker",
                         hours=Decimal("8.00"))
        db.session.add(att)
    db.session.commit()
    return jsonify(ok=True, attendance=s_attendance(att),
                   distance=round(dist, 4))


@bp.route("/attendance/<int:aid>/confirm-vendor", methods=["POST"])
@jwt_required(ROLE_VENDOR_REP, ROLE_ADMIN)
def attendance_confirm_vendor(aid):
    a = Attendance.query.get_or_404(aid)
    d = request.get_json() or {}
    status = d.get("status") or a.status
    hours = d.get("hours")
    a.status = status
    if hours is not None:
        a.hours = Decimal(str(hours))
    ap = a.approval("Vendor") or AttendanceApproval(attendance_id=a.id, side="Vendor")
    ap.status = "Approved"
    ap.decided_by_id = g.api_user.id
    ap.decided_at = datetime.utcnow()
    if not ap.id:
        db.session.add(ap)
    # default Client to Pending
    if not a.approval("Client"):
        db.session.add(AttendanceApproval(
            attendance_id=a.id, side="Client", status="Pending"))
    db.session.commit()
    return jsonify(attendance=s_attendance(a))


@bp.route("/attendance/<int:aid>/approve-client", methods=["POST"])
@jwt_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def attendance_approve_client(aid):
    a = Attendance.query.get_or_404(aid)
    d = request.get_json() or {}
    decision = d.get("decision") or "Approved"
    remark = d.get("remark") or ""
    ap = a.approval("Client") or AttendanceApproval(attendance_id=a.id, side="Client")
    ap.status = decision
    ap.decided_by_id = g.api_user.id
    ap.decided_at = datetime.utcnow()
    ap.remark = remark
    if not ap.id:
        db.session.add(ap)
    db.session.commit()
    return jsonify(attendance=s_attendance(a))


@bp.route("/roster", methods=["GET"])
@jwt_required()
def roster():
    project_id = request.args.get("project", type=int)
    on = parse_date(request.args.get("date")) or date.today()
    if not project_id:
        return jsonify(error="project required"), 400
    rows = Attendance.query.filter_by(project_id=project_id, work_date=on).all()
    return jsonify(date=on.isoformat(), project_id=project_id,
                   rows=[s_attendance(a) for a in rows])


# ---------------------------------------------------------------------------
# Regularization
# ---------------------------------------------------------------------------
@bp.route("/regularization", methods=["POST"])
@jwt_required(ROLE_WORKER, ROLE_VENDOR_REP, ROLE_ADMIN)
def regularization_create():
    d = request.get_json() or {}
    worker_id = d.get("worker_id") or (g.api_user.worker_id if g.api_user.role == ROLE_WORKER else None)
    r = Regularization(
        worker_id=worker_id,
        project_id=int(d["project_id"]),
        work_date=parse_date(d["work_date"]),
        requested_status=d.get("requested_status", ATT_PRESENT),
        reason=d.get("reason"),
        requested_by_id=g.api_user.id,
    )
    db.session.add(r)
    db.session.commit()
    return jsonify(id=r.id, status=r.status), 201


@bp.route("/regularization/<int:rid>/decide", methods=["POST"])
@jwt_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def regularization_decide(rid):
    r = Regularization.query.get_or_404(rid)
    d = request.get_json() or {}
    decision = d.get("decision", "Approved")
    r.status = decision
    r.decided_by_id = g.api_user.id
    r.decided_at = datetime.utcnow()
    r.decision_remark = d.get("remark") or ""
    if decision == "Approved":
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
        for side in ("Vendor", "Client"):
            ap = att.approval(side) or AttendanceApproval(attendance_id=att.id, side=side)
            ap.status = "Approved"
            ap.decided_by_id = g.api_user.id
            ap.decided_at = datetime.utcnow()
            ap.remark = f"regularized by {g.api_user.username}"
            if not ap.id:
                db.session.add(ap)
    db.session.commit()
    return jsonify(id=r.id, status=r.status)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
@bp.route("/invoice/generate", methods=["POST"])
@jwt_required(ROLE_ADMIN)
def invoice_generate_api():
    d = request.get_json() or {}
    ps = parse_date(d["period_start"])
    pe = parse_date(d["period_end"])
    gst = Decimal(str(d["gst_rate"])) if d.get("gst_rate") is not None else None
    tds = Decimal(str(d["tds_rate"])) if d.get("tds_rate") is not None else None
    inv = build_invoice(int(d["agency_id"]), int(d["project_id"]),
                        ps, pe, gst, tds, g.api_user.id)
    return jsonify(invoice=s_invoice(inv)), 201


@bp.route("/invoice/<int:iid>", methods=["GET"])
@jwt_required()
def invoice_get(iid):
    inv = Invoice.query.get_or_404(iid)
    return jsonify(invoice=s_invoice(inv))
