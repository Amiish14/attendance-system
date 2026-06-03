"""Render `release` phase: idempotent DB + admin bootstrap.

Runs ONCE per deploy, before the web process starts. Safe to run repeatedly.
"""
from app import create_app
from models import db, User, ROLE_ADMIN

app = create_app()

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username="admin").first():
        u = User(username="admin", display_name="HR Admin",
                 role=ROLE_ADMIN, is_active=True, must_change_password=True)
        u.set_password("admin123")
        db.session.add(u)
        db.session.commit()
        print("[release] seed admin created (username=admin, password=admin123)")
    else:
        print("[release] admin user already present, nothing to do")
    print("[release] schema ready")
