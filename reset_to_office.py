"""One-shot: wipe all demo data, leave only role accounts + PRERNA-ready setup.

Removes:
  - Workers W001, W002, W003 (the seed demo workers)
  - User vendor1
  - Agency VELO (Velocity Manpower)
  - Project BWN-WH (Bhiwandi Warehouse demo)
  - Rate cards / face templates / attendance / approvals tied to the above

Ensures:
  - Agency PROCAM (in-house) exists, approver_mode='hr_only'
  - Project HO-DEL "Procam Head Office (Janakpuri)" exists, active
  - Skills 'Loader', 'Helper', 'Supervisor' kept ONLY if still referenced;
    otherwise removed (they were demo-only).
  - Role accounts admin / procamrep / gate1 untouched

Run:
    python reset_to_office.py
"""
from datetime import date
from decimal import Decimal

from app import create_app
from models import (db, User, Worker, Agency, Project, Skill, RateCard,
                    Attendance, AttendanceApproval, FaceTemplate, Invoice,
                    InvoiceLine, ROLE_PROCAM_REP, APPROVER_HR_ONLY)


PROCAM_CODE = "PROCAM"
PROCAM_NAME = "Procam Logistics (In-house)"
OFFICE_CODE = "HO-DEL"
OFFICE_NAME = "Procam Head Office (Janakpuri)"


def _delete_worker(w: Worker):
    """Cascade-delete attendance, approvals, face template, user linked to a worker."""
    # Face template
    if w.face_template:
        db.session.delete(w.face_template)
    # Attendance + approvals
    for a in Attendance.query.filter_by(worker_id=w.id).all():
        AttendanceApproval.query.filter_by(attendance_id=a.id).delete()
        db.session.delete(a)
    # User
    u = User.query.filter_by(worker_id=w.id).first()
    if u:
        db.session.delete(u)
    db.session.delete(w)


def reset_to_office():
    app = create_app()
    with app.app_context():
        db.create_all()

        wiped_workers = 0
        wiped_invoices = 0

        # ── 1. Wipe demo workers ─────────────────────────────────────────
        for code in ("W001", "W002", "W003"):
            w = Worker.query.filter_by(code=code).first()
            if w:
                _delete_worker(w); wiped_workers += 1
        db.session.commit()

        # ── 2. Wipe demo agency Velocity (VELO) + its dependencies ───────
        velo = Agency.query.filter_by(code="VELO").first()
        if velo:
            # All rate cards under it
            RateCard.query.filter_by(agency_id=velo.id).delete()
            # All invoices under it
            for inv in Invoice.query.filter_by(agency_id=velo.id).all():
                InvoiceLine.query.filter_by(invoice_id=inv.id).delete()
                db.session.delete(inv); wiped_invoices += 1
            # Workers still under VELO (e.g. legacy demo)
            for w in Worker.query.filter_by(agency_id=velo.id).all():
                _delete_worker(w); wiped_workers += 1
            # Vendor rep account
            User.query.filter_by(agency_id=velo.id).delete()
            db.session.delete(velo)
        db.session.commit()

        # ── 3. Wipe demo project (Bhiwandi Warehouse) ────────────────────
        bwn = Project.query.filter_by(code="BWN-WH").first()
        if bwn:
            # Detach: any RateCard / Attendance pointing here would have been
            # gone already with the VELO cleanup; safe to delete.
            RateCard.query.filter_by(project_id=bwn.id).delete()
            for a in Attendance.query.filter_by(project_id=bwn.id).all():
                AttendanceApproval.query.filter_by(attendance_id=a.id).delete()
                db.session.delete(a)
            for inv in Invoice.query.filter_by(project_id=bwn.id).all():
                InvoiceLine.query.filter_by(invoice_id=inv.id).delete()
                db.session.delete(inv); wiped_invoices += 1
            db.session.delete(bwn)
        db.session.commit()

        # ── 4. Wipe demo-only skills if no worker uses them ──────────────
        for sk_name in ("Loader", "Helper", "Supervisor"):
            sk = Skill.query.filter_by(name=sk_name).first()
            if sk and not Worker.query.filter_by(skill_id=sk.id).first():
                db.session.delete(sk)
        db.session.commit()

        # ── 5. Ensure PROCAM (in-house) agency exists, hr_only mode ──────
        procam = Agency.query.filter_by(code=PROCAM_CODE).first()
        if not procam:
            procam = Agency(
                code=PROCAM_CODE, name=PROCAM_NAME,
                contact_person="HR Admin", email="hr@procamlogistics.com",
                address="731, Westend Mall, District Centre, Janakpuri, New Delhi-110058.",
                default_gst_rate=Decimal("0.00"), default_tds_rate=Decimal("0.00"),
                onboarded_on=date.today(), is_active=True,
                approver_mode=APPROVER_HR_ONLY,
            )
            db.session.add(procam); db.session.flush()
        else:
            procam.approver_mode = APPROVER_HR_ONLY  # force mode in case row pre-existed
        db.session.commit()

        # ── 6. Ensure the office project exists ──────────────────────────
        prep = User.query.filter_by(role=ROLE_PROCAM_REP).first()
        office = Project.query.filter_by(code=OFFICE_CODE).first()
        if not office:
            office = Project(
                code=OFFICE_CODE, name=OFFICE_NAME,
                client_name="Procam Logistics Pvt Ltd",
                location="Janakpuri, New Delhi",
                start_date=date.today(),
                procam_rep_id=prep.id if prep else None,
                is_active=True,
            )
            db.session.add(office)
            db.session.commit()

        print("✓ RESET COMPLETE")
        print(f"   wiped workers   : {wiped_workers}")
        print(f"   wiped invoices  : {wiped_invoices}")
        print(f"   Agency  '{PROCAM_NAME}' [{PROCAM_CODE}] ready (mode=hr_only)")
        print(f"   Project '{OFFICE_NAME}' [{OFFICE_CODE}] ready")
        print()
        print("Next step:")
        print("   python import_prerna.py <path-to-PRERNA-xlsx>")


if __name__ == "__main__":
    reset_to_office()
