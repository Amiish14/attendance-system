"""One-shot: reset every worker's password back to their employee ID, and
   mark them must-change-on-next-login. Run once and you're done.

   Usage (from project root, with .venv active):
       python reset_all_passwords.py

   It operates on whatever DB is currently configured — local SQLite by default,
   or the Render Postgres if you've set DATABASE_URL.
"""
from app import create_app
from models import db, User, ROLE_WORKER

app = create_app()

with app.app_context():
    workers = User.query.filter_by(role=ROLE_WORKER).all()
    if not workers:
        print("[reset] no workers found in the database.")
        print("        run import_prerna.py first to load the 124 PROCAM employees.")
        raise SystemExit(0)

    print(f"[reset] found {len(workers)} workers — resetting passwords now...")
    for u in workers:
        u.set_password(u.username)        # password = emp_code
        u.must_change_password = True     # force change on next login
        u.is_active = True
    db.session.commit()
    print(f"[reset] done. {len(workers)} workers reset.")
    print(f"        every worker logs in with username=password=their emp_code")
    print(f"        e.g. DIR12010 / DIR12010 — system forces a new password on first login.")
