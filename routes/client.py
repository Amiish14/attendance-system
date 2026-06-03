"""Procam Rep / 'client' side — approves the day's roster."""
from datetime import date, datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from models import (
    db, Project, Worker, Attendance, AttendanceApproval,
    ROLE_PROCAM_REP, ROLE_ADMIN,
)
from utils import role_required, parse_date

bp = Blueprint("client", __name__)


@bp.route("/")
@login_required
@role_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def dashboard():
    pending = (AttendanceApproval.query
               .filter_by(side="Client", status="Pending").count())
    today_att = Attendance.query.filter_by(work_date=date.today()).count()
    return render_template("client/dashboard.html",
                           pending=pending, today_att=today_att)


@bp.route("/roster", methods=["GET", "POST"])
@login_required
@role_required(ROLE_PROCAM_REP, ROLE_ADMIN)
def roster():
    projects = Project.query.filter_by(is_active=True).order_by(Project.name).all()
    d = parse_date(request.args.get("date")) or date.today()
    project_id = int(request.args.get("project") or 0)
    project = None
    rows = []
    if project_id:
        project = Project.query.get(project_id)
        # only show attendance where Vendor side is approved (ready for client)
        attendance_rows = (Attendance.query
                           .filter_by(project_id=project_id, work_date=d)
                           .all())
        for a in attendance_rows:
            v = a.approval("Vendor")
            if v and v.status == "Approved":
                rows.append(a)

    if request.method == "POST" and project_id:
        action = request.form.get("bulk_action")
        ids = request.form.getlist("att_id")
        for sid in ids:
            a = Attendance.query.get(int(sid))
            if not a:
                continue
            cap = a.approval("Client") or AttendanceApproval(attendance_id=a.id, side="Client")
            decision = request.form.get(f"decision_{a.id}") or action or "Approved"
            remark = request.form.get(f"remark_{a.id}") or ""
            cap.status = decision
            cap.decided_by_id = current_user.id
            cap.decided_at = datetime.utcnow()
            cap.remark = remark
            if not cap.id:
                db.session.add(cap)
        db.session.commit()
        flash("Approvals saved.", "success")
        return redirect(url_for("client.roster", date=d.isoformat(), project=project_id))

    return render_template("client/roster.html",
                           projects=projects, project=project,
                           selected_date=d, rows=rows, project_id=project_id)
