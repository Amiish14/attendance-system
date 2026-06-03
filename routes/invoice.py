"""Invoice generation, listing, printing."""
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from models import (
    db, Agency, Project, Worker, RateCard,
    Attendance, AttendanceApproval, Invoice, InvoiceLine,
    ATT_PRESENT, ATT_HALF, ATT_REGULARIZED, ROLE_ADMIN,
)
from utils import role_required, parse_date

bp = Blueprint("invoice", __name__)


# ---------------------------------------------------------------------------
def _resolve_rate(agency_id, project_id, skill_id, on_day):
    """Find the RateCard effective on `on_day` for the given (agency,project,skill)."""
    q = (RateCard.query
         .filter(RateCard.agency_id == agency_id,
                 RateCard.project_id == project_id,
                 RateCard.skill_id == skill_id,
                 RateCard.effective_from <= on_day)
         .order_by(RateCard.effective_from.desc()))
    for rc in q.all():
        if rc.effective_to is None or rc.effective_to >= on_day:
            return rc
    return None


def _next_invoice_no(agency: Agency, when: date) -> str:
    yymm = when.strftime("%y-%m")
    prefix = f"PCM/{agency.code}/{yymm}/"
    n = Invoice.query.filter(Invoice.invoice_no.like(prefix + "%")).count() + 1
    return f"{prefix}{n:03d}"


def build_invoice(agency_id: int, project_id: int,
                  period_start: date, period_end: date,
                  gst_rate: Decimal | None, tds_rate: Decimal | None,
                  user_id: int) -> Invoice:
    """Core invoice generation. Returns committed Invoice."""
    agency = Agency.query.get_or_404(agency_id)
    project = Project.query.get_or_404(project_id)

    # All dual-approved attendance in the window for this (agency, project)
    rows = (Attendance.query
            .join(Worker, Worker.id == Attendance.worker_id)
            .filter(Worker.agency_id == agency_id,
                    Attendance.project_id == project_id,
                    Attendance.work_date >= period_start,
                    Attendance.work_date <= period_end)
            .all())
    eligible = [a for a in rows if a.is_dual_approved
                and a.status in (ATT_PRESENT, ATT_HALF, ATT_REGULARIZED)]

    # group by worker
    per_worker: dict[int, dict] = {}
    for a in eligible:
        w = a.worker
        entry = per_worker.setdefault(a.worker_id, {
            "worker": w, "present": 0, "half": 0,
            "rate_total": Decimal("0.00"), "rate_days": 0,
            "line_amount": Decimal("0.00"),
        })
        rc = _resolve_rate(agency_id, project_id, w.skill_id, a.work_date)
        rate = rc.daily_rate if rc else Decimal("0.00")
        if a.status == ATT_HALF:
            entry["half"] += 1
            entry["line_amount"] += rate * Decimal("0.5")
        else:
            entry["present"] += 1
            entry["line_amount"] += rate
        entry["rate_total"] += rate
        entry["rate_days"] += 1

    inv_gst = Decimal(str(gst_rate)) if gst_rate is not None else Decimal(str(agency.default_gst_rate))
    inv_tds = Decimal(str(tds_rate)) if tds_rate is not None else Decimal(str(agency.default_tds_rate))

    inv = Invoice(
        invoice_no=_next_invoice_no(agency, datetime.utcnow().date()),
        agency_id=agency_id,
        project_id=project_id,
        period_start=period_start,
        period_end=period_end,
        gst_rate=inv_gst,
        tds_rate=inv_tds,
        generated_by_id=user_id,
    )
    db.session.add(inv)
    db.session.flush()

    subtotal = Decimal("0.00")
    for wid, e in per_worker.items():
        days = Decimal(e["present"]) + Decimal(e["half"]) * Decimal("0.5")
        avg_rate = (e["rate_total"] / e["rate_days"]) if e["rate_days"] else Decimal("0")
        line = InvoiceLine(
            invoice_id=inv.id,
            worker_id=wid,
            skill=e["worker"].skill.name if e["worker"].skill else "",
            days_present=e["present"],
            half_days=e["half"],
            total_billable_days=days,
            daily_rate=avg_rate.quantize(Decimal("0.01")),
            line_amount=e["line_amount"].quantize(Decimal("0.01")),
        )
        subtotal += line.line_amount
        db.session.add(line)

    gst_amount = (subtotal * inv_gst / Decimal("100")).quantize(Decimal("0.01"))
    tds_amount = (subtotal * inv_tds / Decimal("100")).quantize(Decimal("0.01"))
    inv.subtotal = subtotal.quantize(Decimal("0.01"))
    inv.gst_amount = gst_amount
    inv.tds_amount = tds_amount
    inv.net_payable = (subtotal + gst_amount - tds_amount).quantize(Decimal("0.01"))
    db.session.commit()
    return inv


# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
@role_required(ROLE_ADMIN)
def list_invoices():
    rows = Invoice.query.order_by(Invoice.generated_at.desc()).all()
    return render_template("invoice/list.html", rows=rows)


@bp.route("/generate", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def generate():
    agencies = Agency.query.filter_by(is_active=True).all()
    projects = Project.query.filter_by(is_active=True).all()

    if request.method == "POST":
        f = request.form
        try:
            agency_id = int(f["agency_id"])
            project_id = int(f["project_id"])
            ps = parse_date(f["period_start"])
            pe = parse_date(f["period_end"])
            gst = Decimal(f.get("gst_rate") or "0") if f.get("gst_rate") else None
            tds = Decimal(f.get("tds_rate") or "0") if f.get("tds_rate") else None
        except Exception as e:
            flash(f"Invalid input: {e}", "error")
            return redirect(url_for("invoice.generate"))
        inv = build_invoice(agency_id, project_id, ps, pe, gst, tds, current_user.id)
        flash(f"Invoice {inv.invoice_no} generated.", "success")
        return redirect(url_for("invoice.print_invoice", iid=inv.id))

    return render_template("invoice/generate.html", agencies=agencies, projects=projects)


@bp.route("/<int:iid>/print")
@login_required
@role_required(ROLE_ADMIN)
def print_invoice(iid):
    inv = Invoice.query.get_or_404(iid)

    # audit log: for each worker line, list approved days
    audit = {}
    for ln in inv.lines:
        days = (Attendance.query
                .filter(Attendance.worker_id == ln.worker_id,
                        Attendance.project_id == inv.project_id,
                        Attendance.work_date >= inv.period_start,
                        Attendance.work_date <= inv.period_end)
                .order_by(Attendance.work_date).all())
        audit[ln.worker_id] = [a for a in days if a.is_dual_approved]
    return render_template("invoice/print.html", inv=inv, audit=audit)


@bp.route("/<int:iid>/issue", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def issue(iid):
    inv = Invoice.query.get_or_404(iid)
    inv.status = "Issued"
    db.session.commit()
    flash("Invoice marked as Issued.", "success")
    return redirect(url_for("invoice.print_invoice", iid=iid))


@bp.route("/<int:iid>/mark-paid", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def mark_paid(iid):
    inv = Invoice.query.get_or_404(iid)
    inv.status = "Paid"
    db.session.commit()
    flash("Invoice marked Paid.", "success")
    return redirect(url_for("invoice.print_invoice", iid=iid))
