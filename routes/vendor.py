"""Vendor Rep blueprint — confirms day's roster, onboards workers."""
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from models import (
    db, Agency, Project, Worker, Skill, Attendance, AttendanceApproval,
    ATT_PRESENT, ATT_ABSENT, ATT_HALF, ROLE_VENDOR_REP, ROLE_ADMIN, User,
)
from utils import role_required, parse_date

bp = Blueprint("vendor", __name__)


# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
@role_required(ROLE_VENDOR_REP, ROLE_ADMIN)
def dashboard():
    agency_id = current_user.agency_id
    workers_count = Worker.query.filter_by(agency_id=agency_id, is_active=True).count() if agency_id else 0
    today_att = (Attendance.query
                 .join(Worker)
                 .filter(Worker.agency_id == agency_id,
                         Attendance.work_date == date.today()).count()) if agency_id else 0
    return render_template("vendor/dashboard.html",
                           workers_count=workers_count, today_att=today_att)


# ---------------------------------------------------------------------------
@bp.route("/workers")
@login_required
@role_required(ROLE_VENDOR_REP, ROLE_ADMIN)
def workers():
    q = Worker.query
    if current_user.role == ROLE_VENDOR_REP:
        q = q.filter_by(agency_id=current_user.agency_id)
    rows = q.order_by(Worker.full_name).all()
    return render_template("vendor/workers.html", rows=rows)


@bp.route("/workers/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_VENDOR_REP, ROLE_ADMIN)
def worker_new():
    skills = Skill.query.order_by(Skill.name).all()
    if request.method == "POST":
        f = request.form
        agency_id = current_user.agency_id or int(f.get("agency_id") or 0)
        if not agency_id:
            flash("Agency not set on your user.", "error")
            return redirect(url_for("vendor.workers"))
        w = Worker(
            code=(f.get("code") or "").strip(),
            full_name=(f.get("full_name") or "").strip(),
            agency_id=agency_id,
            skill_id=int(f["skill_id"]) if f.get("skill_id") else None,
            gender=f.get("gender") or "",
            mobile=f.get("mobile") or "",
            aadhaar=f.get("aadhaar") or "",
            bank_name=f.get("bank_name") or "",
            account_no=f.get("account_no") or "",
            ifsc=f.get("ifsc") or "",
            onboarded_on=date.today(),
        )
        db.session.add(w)
        db.session.flush()
        # create worker login if asked
        if f.get("create_login"):
            from models import ROLE_WORKER
            u = User(
                username=f.get("username") or w.code,
                display_name=w.full_name,
                role=ROLE_WORKER,
                worker_id=w.id,
                agency_id=w.agency_id,
                must_change_password=True,
            )
            u.set_password(f.get("password") or w.code)
            db.session.add(u)
        db.session.commit()
        flash(f"Worker {w.full_name} added.", "success")
        return redirect(url_for("vendor.workers"))
    return render_template("vendor/worker_form.html", skills=skills)


# ---------------------------------------------------------------------------
# Daily roster — Vendor confirmation step
# ---------------------------------------------------------------------------
@bp.route("/roster", methods=["GET", "POST"])
@login_required
@role_required(ROLE_VENDOR_REP, ROLE_ADMIN)
def roster():
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    d = parse_date(request.args.get("date")) or date.today()
    project_id = int(request.args.get("project") or 0)
    agency_id = current_user.agency_id

    rows = []
    project = None
    if project_id and agency_id:
        project = Project.query.get(project_id)
        # all workers in this agency (could later filter by ProjectAssignment)
        workers = (Worker.query
                   .filter_by(agency_id=agency_id, is_active=True)
                   .order_by(Worker.full_name).all())
        # existing attendance for the day
        existing = {a.worker_id: a for a in
                    Attendance.query.filter_by(project_id=project_id, work_date=d)
                    .join(Worker).filter(Worker.agency_id == agency_id).all()}
        for w in workers:
            rows.append({"worker": w, "att": existing.get(w.id)})

    if request.method == "POST" and project_id:
        project = Project.query.get_or_404(project_id)
        workers = (Worker.query
                   .filter_by(agency_id=current_user.agency_id, is_active=True).all())
        for w in workers:
            status = request.form.get(f"status_{w.id}")
            hours = request.form.get(f"hours_{w.id}") or "8"
            if not status:
                continue
            att = Attendance.query.filter_by(
                worker_id=w.id, project_id=project_id, work_date=d).first()
            if not att:
                att = Attendance(worker_id=w.id, project_id=project_id,
                                 work_date=d, source="VendorRep")
                db.session.add(att)
            att.status = status
            try:
                att.hours = Decimal(hours)
            except Exception:
                att.hours = Decimal("8")
            db.session.flush()
            # set vendor approval = Approved
            vap = att.approval("Vendor")
            if not vap:
                vap = AttendanceApproval(attendance_id=att.id, side="Vendor")
                db.session.add(vap)
            vap.status = "Approved"
            vap.decided_by_id = current_user.id
            vap.decided_at = datetime.utcnow()
            # client side default to Pending
            cap = att.approval("Client")
            if not cap:
                db.session.add(AttendanceApproval(
                    attendance_id=att.id, side="Client", status="Pending"))
        db.session.commit()
        flash("Roster confirmed and sent to Procam Rep for approval.", "success")
        return redirect(url_for("vendor.roster", date=d.isoformat(), project=project_id))

    return render_template("vendor/roster.html",
                           projects=projects, project=project,
                           selected_date=d, rows=rows, project_id=project_id)
