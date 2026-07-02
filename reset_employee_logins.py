"""One-shot: reset EVERY employee's login to their Employee ID.

   Policy (matches the Admin -> Users "Reset employee logins" button):
     * password        = the employee's ID (their linked Worker.code)
     * username        = left UNCHANGED
     * must_change_password = True  (forced new password on next login)

   Covers ALL employees regardless of role (Admin / ProcamRep / Worker) — every
   User account that is linked to a Worker record. System accounts with no
   Worker link (bootstrap admin, gate guard) are left untouched.

   Usage (from project root, with .venv active):
       python reset_employee_logins.py

   Operates on whatever DB is configured: local SQLite by default, or the
   Render Postgres if DATABASE_URL is set in the environment.
"""
from app import create_app
from models import db, User

app = create_app()

with app.app_context():
    users = User.query.filter(User.worker_id.isnot(None)).all()
    if not users:
        print("[reset] no employees (worker-linked users) found.")
        print("        run import_prerna.py first to load the PROCAM employees.")
        raise SystemExit(0)

    print(f"[reset] found {len(users)} employee login(s) — resetting now...")
    n, skipped = 0, 0
    for u in users:
        w = u.worker
        code = (w.code if w else "") or ""
        if not code.strip():
            skipped += 1
            continue
        u.set_password(code)            # password = Employee ID
        u.must_change_password = True   # force change on next login
        u.is_active = True
        n += 1
    db.session.commit()

    print(f"[reset] done. {n} employee login(s) reset"
          + (f", {skipped} skipped (no code)." if skipped else "."))
    print("        Each employee now logs in with their Employee ID as the")
    print("        password (username unchanged) and must set a new one on")
    print("        first login. e.g. EMP1552018 -> password EMP1552018")
