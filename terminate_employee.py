"""Termination script — fully removes an employee from the system.

   Wipes their login account, worker record, face template, all attendance,
   project assignments, and regularization requests. Reusable for any future
   termination — just pass the emp_code.

   For safety:
     - Defaults to DRY-RUN. Shows you exactly what will be deleted.
     - Add --confirm to actually delete.
     - Refuses to delete InvoiceLine rows that belong to an Issued / Paid
       invoice (those are accounting records). If found, prints a warning so
       you can deal with them manually.

   Usage:
       python terminate_employee.py EMP_CODE              # dry-run (preview)
       python terminate_employee.py EMP_CODE --confirm    # actually delete

   Example:
       python terminate_employee.py EMP192010
       python terminate_employee.py EMP192010 --confirm
"""
import sys
from app import create_app
from models import (db, User, Worker, FaceTemplate,
                    Attendance, AttendanceApproval, Regularization,
                    ProjectAssignment, InvoiceLine, Invoice)


def terminate(emp_code: str, confirm: bool):
    app = create_app()
    with app.app_context():
        u = User.query.filter_by(username=emp_code).first()
        w = Worker.query.filter_by(code=emp_code).first()

        if not u and not w:
            sys.exit(f"No user or worker found with emp_code '{emp_code}'.")

        print(f"\n--- Termination report for {emp_code} ---")
        print(f"  Full name      : {(u.display_name if u else w.full_name) or '?'}")
        print(f"  User role      : {u.role if u else '(no User row)'}")
        print(f"  User id        : {u.id if u else '—'}")
        print(f"  Worker id      : {w.id if w else '—'}")

        # Count everything that will be removed
        if w:
            face_n   = FaceTemplate.query.filter_by(worker_id=w.id).count()
            att_q    = Attendance.query.filter_by(worker_id=w.id)
            att_n    = att_q.count()
            approval_n = AttendanceApproval.query \
                .join(Attendance, Attendance.id == AttendanceApproval.attendance_id) \
                .filter(Attendance.worker_id == w.id).count() if att_n else 0
            reg_n    = Regularization.query.filter_by(worker_id=w.id).count()
            assign_n = ProjectAssignment.query.filter_by(worker_id=w.id).count()

            # Invoice lines — these are the dicey ones (accounting records).
            inv_lines = InvoiceLine.query.filter_by(worker_id=w.id).all()
            locked_lines = []
            free_lines = []
            for ln in inv_lines:
                inv = Invoice.query.get(ln.invoice_id)
                if inv and inv.status in ("Issued", "Paid"):
                    locked_lines.append((ln, inv))
                else:
                    free_lines.append(ln)

            print(f"\n  Linked records:")
            print(f"    Face template rows       : {face_n}")
            print(f"    Attendance rows          : {att_n}")
            print(f"    Attendance approval rows : {approval_n}  (auto-cascade)")
            print(f"    Regularization requests  : {reg_n}")
            print(f"    Project assignments      : {assign_n}")
            print(f"    Invoice lines (deletable): {len(free_lines)}")
            print(f"    Invoice lines (LOCKED)   : {len(locked_lines)}  "
                  f"(belong to Issued/Paid invoices)")
            if locked_lines:
                print(f"\n  ⚠️  Cannot delete these invoice lines — they belong to issued "
                      f"or paid invoices and removing them would corrupt accounting:")
                for ln, inv in locked_lines[:10]:
                    print(f"      InvoiceLine#{ln.id}  Invoice {inv.invoice_no} ({inv.status})")
                if len(locked_lines) > 10:
                    print(f"      ... and {len(locked_lines) - 10} more")
                print(f"  → handle these manually before re-running with --confirm.")
        else:
            face_n = att_n = approval_n = reg_n = assign_n = 0
            free_lines = []; locked_lines = []

        if not confirm:
            print(f"\n  This is a DRY-RUN. Nothing was deleted.")
            print(f"  Re-run with --confirm to actually delete everything above:")
            print(f"      python terminate_employee.py {emp_code} --confirm")
            return

        if locked_lines:
            sys.exit(f"\nAborting — {len(locked_lines)} invoice lines are locked. "
                     f"Cancel or void those invoices first, then re-run.")

        # Do the deletion in child-first order to avoid FK violations
        print(f"\n  Deleting...")
        if w:
            # Attendance — cascade handles approvals
            n = Attendance.query.filter_by(worker_id=w.id).delete(synchronize_session=False)
            print(f"    deleted {n} attendance rows")
            n = Regularization.query.filter_by(worker_id=w.id).delete(synchronize_session=False)
            print(f"    deleted {n} regularization rows")
            n = ProjectAssignment.query.filter_by(worker_id=w.id).delete(synchronize_session=False)
            print(f"    deleted {n} project assignments")
            n = FaceTemplate.query.filter_by(worker_id=w.id).delete(synchronize_session=False)
            print(f"    deleted {n} face template rows")
            for ln in free_lines:
                db.session.delete(ln)
            if free_lines:
                print(f"    deleted {len(free_lines)} invoice lines (from Draft invoices)")

        if u:
            # Detach the User from the Worker before deleting Worker (else FK trips)
            u.worker_id = None
            db.session.flush()
            db.session.delete(u)
            print(f"    deleted User account '{u.username}'")
        if w:
            db.session.delete(w)
            print(f"    deleted Worker record '{w.code}'")

        db.session.commit()
        print(f"\n✅ Termination complete for {emp_code}.\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    emp = args[0]
    confirm = "--confirm" in args[1:]
    terminate(emp, confirm)
