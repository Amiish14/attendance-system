"""Admin blueprint — onboards agencies, projects, skills, rate cards, workers."""
from datetime import date
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from models import (
    db, User, Agency, Project, Skill, RateCard, Worker,
    Attendance, Invoice, ROLE_ADMIN, ROLE_PROCAM_REP, ROLE_VENDOR_REP, ROLE_WORKER,
)
from utils import role_required, parse_date

bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
@role_required(ROLE_ADMIN)
def dashboard():
    stats = {
        "agencies": Agency.query.filter_by(is_active=True).count(),
        "projects": Project.query.filter_by(is_active=True).count(),
        "workers": Worker.query.filter_by(is_active=True).count(),
        "invoices": Invoice.query.count(),
        "attendance_today": Attendance.query.filter_by(work_date=date.today()).count(),
    }
    return render_template("admin/dashboard.html", stats=stats)


# ---------------------------------------------------------------------------
# Agencies
# ---------------------------------------------------------------------------
@bp.route("/agencies")
@login_required
@role_required(ROLE_ADMIN)
def agencies():
    rows = Agency.query.order_by(Agency.name).all()
    return render_template("admin/agencies.html", rows=rows)


@bp.route("/agencies/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def agency_new():
    if request.method == "POST":
        f = request.form
        a = Agency(
            code=(f.get("code") or "").strip(),
            name=(f.get("name") or "").strip(),
            contact_person=f.get("contact_person") or "",
            email=f.get("email") or "",
            phone=f.get("phone") or "",
            address=f.get("address") or "",
            gstin=f.get("gstin") or "",
            pan=f.get("pan") or "",
            bank_name=f.get("bank_name") or "",
            account_no=f.get("account_no") or "",
            ifsc=f.get("ifsc") or "",
            default_tds_rate=Decimal(f.get("default_tds_rate") or "2.00"),
            default_gst_rate=Decimal(f.get("default_gst_rate") or "18.00"),
            onboarded_on=parse_date(f.get("onboarded_on")) or date.today(),
        )
        db.session.add(a)
        db.session.commit()
        flash(f"Agency '{a.name}' onboarded.", "success")
        return redirect(url_for("admin.agencies"))
    return render_template("admin/agency_form.html", a=None)


@bp.route("/agencies/<int:aid>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def agency_edit(aid):
    a = Agency.query.get_or_404(aid)
    if request.method == "POST":
        f = request.form
        a.code = (f.get("code") or "").strip()
        a.name = (f.get("name") or "").strip()
        a.contact_person = f.get("contact_person") or ""
        a.email = f.get("email") or ""
        a.phone = f.get("phone") or ""
        a.address = f.get("address") or ""
        a.gstin = f.get("gstin") or ""
        a.pan = f.get("pan") or ""
        a.bank_name = f.get("bank_name") or ""
        a.account_no = f.get("account_no") or ""
        a.ifsc = f.get("ifsc") or ""
        a.default_tds_rate = Decimal(f.get("default_tds_rate") or "2.00")
        a.default_gst_rate = Decimal(f.get("default_gst_rate") or "18.00")
        a.is_active = bool(f.get("is_active"))
        db.session.commit()
        flash("Agency updated.", "success")
        return redirect(url_for("admin.agencies"))
    return render_template("admin/agency_form.html", a=a)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@bp.route("/projects")
@login_required
@role_required(ROLE_ADMIN)
def projects():
    rows = Project.query.order_by(Project.name).all()
    return render_template("admin/projects.html", rows=rows)


@bp.route("/projects/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def project_new():
    procam_reps = User.query.filter_by(role=ROLE_PROCAM_REP, is_active=True).all()
    if request.method == "POST":
        f = request.form
        p = Project(
            code=(f.get("code") or "").strip(),
            name=(f.get("name") or "").strip(),
            client_name=f.get("client_name") or "",
            location=f.get("location") or "",
            start_date=parse_date(f.get("start_date")),
            end_date=parse_date(f.get("end_date")),
            procam_rep_id=int(f.get("procam_rep_id")) if f.get("procam_rep_id") else None,
        )
        db.session.add(p)
        db.session.commit()
        flash(f"Project '{p.name}' created.", "success")
        return redirect(url_for("admin.projects"))
    return render_template("admin/project_form.html", p=None, procam_reps=procam_reps)


@bp.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def project_edit(pid):
    p = Project.query.get_or_404(pid)
    procam_reps = User.query.filter_by(role=ROLE_PROCAM_REP, is_active=True).all()
    if request.method == "POST":
        f = request.form
        p.code = (f.get("code") or "").strip()
        p.name = (f.get("name") or "").strip()
        p.client_name = f.get("client_name") or ""
        p.location = f.get("location") or ""
        p.start_date = parse_date(f.get("start_date"))
        p.end_date = parse_date(f.get("end_date"))
        p.procam_rep_id = int(f.get("procam_rep_id")) if f.get("procam_rep_id") else None
        p.is_active = bool(f.get("is_active"))
        db.session.commit()
        flash("Project updated.", "success")
        return redirect(url_for("admin.projects"))
    return render_template("admin/project_form.html", p=p, procam_reps=procam_reps)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
@bp.route("/skills", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def skills():
    from models import WORKER_CATEGORIES, CAT_UNSKILLED
    if request.method == "POST":
        # Either add a new skill or update an existing one's category
        edit_id = request.form.get("edit_id", type=int)
        if edit_id:
            sk = Skill.query.get_or_404(edit_id)
            new_cat = (request.form.get("category") or CAT_UNSKILLED).strip()
            if new_cat not in WORKER_CATEGORIES:
                flash("Invalid category.", "warning")
            else:
                sk.category = new_cat
                db.session.commit()
                flash(f"Skill '{sk.name}' → {new_cat}.", "success")
        else:
            name = (request.form.get("name") or "").strip()
            cat = (request.form.get("category") or CAT_UNSKILLED).strip()
            if cat not in WORKER_CATEGORIES:
                cat = CAT_UNSKILLED
            if name and not Skill.query.filter_by(name=name).first():
                db.session.add(Skill(name=name, category=cat))
                db.session.commit()
                flash(f"Skill '{name}' added ({cat}).", "success")
        return redirect(url_for("admin.skills"))
    rows = Skill.query.order_by(Skill.category, Skill.name).all()
    return render_template("admin/skills.html", rows=rows, categories=WORKER_CATEGORIES)


# ---------------------------------------------------------------------------
# Rate cards
# ---------------------------------------------------------------------------
@bp.route("/rates")
@login_required
@role_required(ROLE_ADMIN)
def rates():
    rows = RateCard.query.order_by(RateCard.effective_from.desc()).all()
    return render_template("admin/rates.html", rows=rows)


@bp.route("/rates/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def rate_new():
    if request.method == "POST":
        f = request.form
        rc = RateCard(
            agency_id=int(f["agency_id"]),
            project_id=int(f["project_id"]),
            skill_id=int(f["skill_id"]),
            daily_rate=Decimal(f.get("daily_rate") or "0"),
            ot_hourly_rate=Decimal(f.get("ot_hourly_rate") or "0"),
            effective_from=parse_date(f.get("effective_from")) or date.today(),
            effective_to=parse_date(f.get("effective_to")),
        )
        db.session.add(rc)
        try:
            db.session.commit()
            flash("Rate card added.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Could not save: {e}", "error")
        return redirect(url_for("admin.rates"))
    return render_template(
        "admin/rate_form.html",
        agencies=Agency.query.filter_by(is_active=True).all(),
        projects=Project.query.filter_by(is_active=True).all(),
        skills=Skill.query.order_by(Skill.name).all(),
    )


# ---------------------------------------------------------------------------
# Workers (admin can also onboard)
# ---------------------------------------------------------------------------
@bp.route("/workers")
@login_required
@role_required(ROLE_ADMIN)
def workers():
    rows = Worker.query.order_by(Worker.full_name).all()
    return render_template("admin/workers.html", rows=rows)


@bp.route("/workers/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def worker_new():
    if request.method == "POST":
        f = request.form
        w = Worker(
            code=(f.get("code") or "").strip(),
            full_name=(f.get("full_name") or "").strip(),
            agency_id=int(f["agency_id"]),
            skill_id=int(f["skill_id"]) if f.get("skill_id") else None,
            gender=f.get("gender") or "",
            mobile=f.get("mobile") or "",
            aadhaar=f.get("aadhaar") or "",
            bank_name=f.get("bank_name") or "",
            account_no=f.get("account_no") or "",
            ifsc=f.get("ifsc") or "",
            onboarded_on=parse_date(f.get("onboarded_on")) or date.today(),
        )
        db.session.add(w)
        db.session.flush()

        # create a login for the worker
        if f.get("create_login"):
            uname = f.get("username") or w.code
            u = User(
                username=uname,
                display_name=w.full_name,
                role=ROLE_WORKER,
                worker_id=w.id,
                agency_id=w.agency_id,
                must_change_password=True,
            )
            u.set_password(f.get("password") or w.code)
            db.session.add(u)
        db.session.commit()
        flash(f"Worker {w.full_name} created.", "success")
        return redirect(url_for("admin.workers"))
    return render_template(
        "admin/worker_form.html",
        agencies=Agency.query.filter_by(is_active=True).all(),
        skills=Skill.query.order_by(Skill.name).all(),
    )


# ---------------------------------------------------------------------------
# Users (light list — admin can create Procam Reps & Vendor Reps here)
# ---------------------------------------------------------------------------
@bp.route("/users")
@login_required
@role_required(ROLE_ADMIN)
def users():
    rows = User.query.order_by(User.username).all()
    return render_template("admin/users.html", rows=rows)


@bp.route("/users/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def user_new():
    if request.method == "POST":
        f = request.form
        if User.query.filter_by(username=f["username"]).first():
            flash("Username already exists.", "error")
        else:
            u = User(
                username=f["username"].strip(),
                email=f.get("email") or "",
                display_name=f.get("display_name") or f["username"],
                role=f.get("role") or ROLE_PROCAM_REP,
                agency_id=int(f["agency_id"]) if f.get("agency_id") else None,
                must_change_password=True,
            )
            u.set_password(f.get("password") or "changeme")
            db.session.add(u)
            db.session.commit()
            flash("User created.", "success")
            return redirect(url_for("admin.users"))
    return render_template(
        "admin/user_form.html",
        agencies=Agency.query.filter_by(is_active=True).all(),
    )


# ---------------------------------------------------------------------------
# Daily attendance — admin sheet
# ---------------------------------------------------------------------------
@bp.route("/attendance/daily")
@login_required
@role_required(ROLE_ADMIN)
def attendance_daily():
    """Browse + download per-day attendance."""
    from models import AttendanceApproval
    d = parse_date(request.args.get("date")) or date.today()
    rows = (Attendance.query.filter_by(work_date=d)
            .join(Worker, Worker.id == Attendance.worker_id)
            .order_by(Worker.code).all())
    return render_template("admin/attendance_daily.html", rows=rows, the_date=d)


@bp.route("/attendance/daily.xlsx")
@login_required
@role_required(ROLE_ADMIN)
def attendance_daily_xlsx():
    """One-click Excel of the day's attendance — for HR to circulate."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from flask import send_file
    import io as _io

    d = parse_date(request.args.get("date")) or date.today()
    rows = (Attendance.query.filter_by(work_date=d)
            .join(Worker, Worker.id == Attendance.worker_id)
            .order_by(Worker.code).all())

    wb = Workbook(); ws = wb.active
    ws.title = f"Attendance {d:%d-%b-%y}"[:30]
    red = PatternFill("solid", fgColor="BC1D2F")
    white_bold = Font(bold=True, color="FFFFFF")

    # Title banner
    title = f"PROCAM — Daily Attendance · {d.strftime('%A, %d %B %Y')}"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=11)
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, color="FFFFFF", size=13)
    ws.cell(row=1, column=1).fill = red
    ws.cell(row=1, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    headers = ["Code", "Worker", "Department / Vertical", "Designation", "Category",
               "IN", "OUT", "Hours", "Vendor Approval", "Procam Approval", "Overall"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=2, column=i, value=h)
        c.font = white_bold; c.fill = red
        c.alignment = Alignment(horizontal="center", vertical="center")

    def _fmt_t(dt):
        return dt.strftime("%H:%M:%S") if dt else ""

    for i, a in enumerate(rows, start=3):
        w = a.worker
        v = a.approval("Vendor"); cap = a.approval("Client")
        vs = v.status if v else "—"
        cs = cap.status if cap else "—"
        overall = "Approved" if (vs == "Approved" and cs == "Approved") else \
                  "Declined" if "Declined" in (vs, cs) else "Pending"
        ws.cell(row=i, column=1, value=w.code if w else "")
        ws.cell(row=i, column=2, value=w.full_name if w else "")
        ws.cell(row=i, column=3, value=(w.agency.name if w and w.agency else ""))
        ws.cell(row=i, column=4, value=(w.skill.name if w and w.skill else ""))
        ws.cell(row=i, column=5, value=(w.category if w else ""))
        ws.cell(row=i, column=6, value=_fmt_t(a.punch_in_at) or _fmt_t(a.captured_at))
        ws.cell(row=i, column=7, value=_fmt_t(a.punch_out_at))
        ws.cell(row=i, column=8, value=float(a.hours or 0))
        ws.cell(row=i, column=9, value=vs)
        ws.cell(row=i, column=10, value=cs)
        ws.cell(row=i, column=11, value=overall)

    # Footer summary
    sr = len(rows) + 4
    ws.cell(row=sr, column=1, value="Summary").font = Font(bold=True)
    n_present = sum(1 for a in rows if a.status == "Present")
    n_appr = sum(1 for a in rows if a.is_dual_approved)
    ws.cell(row=sr + 1, column=1, value="Rows total").font = Font(bold=True)
    ws.cell(row=sr + 1, column=2, value=len(rows))
    ws.cell(row=sr + 2, column=1, value="Present").font = Font(bold=True)
    ws.cell(row=sr + 2, column=2, value=n_present)
    ws.cell(row=sr + 3, column=1, value="Fully approved (billable)").font = Font(bold=True)
    ws.cell(row=sr + 3, column=2, value=n_appr)

    widths = [12, 28, 22, 22, 14, 11, 11, 8, 14, 14, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
    fn = f"Procam_Attendance_{d.strftime('%Y-%m-%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fn,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# HR Approval queue (for hr_only agencies — i.e. Procam in-house)
# ---------------------------------------------------------------------------
@bp.route("/attendance/approvals", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def attendance_approvals():
    """HR approves office-employee punches (Client side). Pending only."""
    from datetime import datetime
    from models import AttendanceApproval, Agency, APPROVER_HR_ONLY

    if request.method == "POST":
        action = request.form.get("action")
        att_ids = request.form.getlist("att_id", type=int)
        if not att_ids:
            flash("Pick at least one row.", "warning")
            return redirect(url_for("admin.attendance_approvals"))

        new_status = "Approved" if action == "approve" else \
                     "Declined" if action == "decline" else None
        if new_status:
            for aid in att_ids:
                att = Attendance.query.get(aid)
                if not att:
                    continue
                ap = att.approval("Client")
                if not ap:
                    ap = AttendanceApproval(attendance_id=att.id, side="Client")
                    db.session.add(ap)
                ap.status = new_status
                ap.decided_by_id = current_user.id
                ap.decided_at = datetime.utcnow()
            db.session.commit()
            flash(f"{len(att_ids)} punch(es) {new_status.lower()}.", "success")
        return redirect(url_for("admin.attendance_approvals"))

    d = parse_date(request.args.get("date")) or date.today()
    # Pull rows where the worker's agency is HR-only AND the Client approval is Pending/missing
    hr_agencies = [a.id for a in Agency.query.filter_by(approver_mode=APPROVER_HR_ONLY).all()]
    q = (Attendance.query
         .join(Worker, Worker.id == Attendance.worker_id)
         .filter(Worker.agency_id.in_(hr_agencies),
                 Attendance.work_date == d))
    pending = [a for a in q.order_by(Worker.code).all()
               if not (a.approval("Client") and a.approval("Client").status == "Approved")]
    decided_today = [a for a in q.all() if a.is_dual_approved]
    return render_template("admin/attendance_approvals.html",
                           pending=pending, decided=decided_today, the_date=d)
