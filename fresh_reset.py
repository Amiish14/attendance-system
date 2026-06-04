"""Fresh reset — for repeated testing. Wipes face data + attendance history
   and resets every employee's password back to their username (emp_code).

   What it does:
     1) Resets every PRERNA-imported user (Admin / ProcamRep / Worker):
          - password  = username (e.g. EMP1552018 / EMP1552018)
          - must_change_password = True   (forced to set a new one on first login)
          - is_active = True
     2) Wipes every face template — every user re-enrols their face on next login.
     3) Wipes attendance + approval + regularization rows (clean daily logs).
     4) Leaves the bootstrap 'admin' / 'admin123' account untouched so you can
        always sign in as HR.
     5) Leaves the gate1 kiosk-operator user untouched.

   Safety:
     Defaults to DRY-RUN. Shows you what it WILL do. Add --confirm to commit.

   Usage:
       python fresh_reset.py                # preview (dry run)
       python fresh_reset.py --confirm      # actually reset

   Run it as often as you like during testing.
"""
import sys
from app import create_app
from models import (db, User, Agency, FaceTemplate,
                    Attendance, AttendanceApproval, Regularization,
                    APPROVER_NONE, APPROVER_DUAL)

# Users we should NOT touch — bootstrap accounts that aren't real employees.
KEEP_USERS = {"admin", "gate1"}


def fresh_reset(confirm: bool):
    app = create_app()
    with app.app_context():
        # Pre-count for the report
        all_users = User.query.all()
        affected = [u for u in all_users if u.username not in KEEP_USERS]

        face_n   = FaceTemplate.query.count()
        att_n    = Attendance.query.count()
        appr_n   = AttendanceApproval.query.count()
        reg_n    = Regularization.query.count()

        print(f"\n=== FRESH RESET — {'DRY-RUN' if not confirm else 'EXECUTING'} ===\n")
        print(f"Users (total in DB)      : {len(all_users)}")
        print(f"Users that WILL be reset : {len(affected)}")
        print(f"Users LEFT UNTOUCHED     : {len(all_users) - len(affected)}  (bootstrap/system)")
        for kept in KEEP_USERS:
            still = User.query.filter_by(username=kept).first()
            if still:
                print(f"   ✓ '{kept}' preserved ({still.role})")
        print()
        print(f"Data that WILL be wiped:")
        print(f"   Face templates       : {face_n}")
        print(f"   Attendance rows      : {att_n}")
        print(f"   Approval rows        : {appr_n}  (auto-cascade with attendance)")
        print(f"   Regularization rows  : {reg_n}")
        print()

        if not confirm:
            print(f"This is a DRY-RUN — nothing was changed.")
            print(f"Re-run with --confirm to actually do it:")
            print(f"    python fresh_reset.py --confirm\n")
            return

        # 1) Wipe attendance (cascade auto-deletes AttendanceApproval rows)
        n = Attendance.query.delete(synchronize_session=False)
        print(f"   ✓ deleted {n} attendance rows (+ approvals via cascade)")

        # 2) Wipe regularizations
        n = Regularization.query.delete(synchronize_session=False)
        print(f"   ✓ deleted {n} regularization rows")

        # 3) Wipe face templates
        n = FaceTemplate.query.delete(synchronize_session=False)
        print(f"   ✓ deleted {n} face templates (everyone re-enrols)")

        # 4) Reset every affected user's password back to their username
        reset_n = 0
        for u in affected:
            u.set_password(u.username)
            u.must_change_password = True
            u.is_active = True
            reset_n += 1
        print(f"   ✓ reset {reset_n} user passwords back to their username")

        # 5) Make sure agency approver modes are correct:
        #    PROCAM (in-house) → 'none' (no approval needed, punch is the record)
        #    NPR    (vendors)  → 'dual' (Contractor + Procam Rep)
        for ag in Agency.query.all():
            if ag.code == "PROCAM" and ag.approver_mode != APPROVER_NONE:
                ag.approver_mode = APPROVER_NONE
                print(f"   ✓ PROCAM agency → approver_mode = none")
            elif ag.code == "NPR" and ag.approver_mode != APPROVER_DUAL:
                ag.approver_mode = APPROVER_DUAL
                print(f"   ✓ NPR agency → approver_mode = dual")

        db.session.commit()

        print(f"\n✅ Fresh reset complete.\n")
        print(f"   Everyone logs in with username = password = their emp_code.")
        print(f"   On first login → forced password change → forced face re-enrol")
        print(f"   → role-appropriate dashboard.\n")
        print(f"   You can also log in as HR: 'admin' / 'admin123'\n")


if __name__ == "__main__":
    confirm = "--confirm" in sys.argv[1:]
    fresh_reset(confirm)
