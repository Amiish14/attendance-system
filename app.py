"""Procam Attendance System — application factory."""
import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
from flask_migrate import Migrate

from config import Config
from models import (db, User, ROLE_ADMIN, ROLE_PROCAM_REP, ROLE_VENDOR_REP,
                    ROLE_WORKER, ROLE_GATE_GUARD)
from utils import indian_commas, amount_in_words

login_manager = LoginManager()
migrate = Migrate()


def create_app(config_class=Config):
    # instance_relative_config makes Flask create instance/ automatically
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    # ensure instance dir exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        pass

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(uid):
        return User.query.get(int(uid))

    # Jinja helpers
    app.jinja_env.globals.update(
        APP_NAME=app.config["APP_NAME"],
        APP_TAGLINE=app.config["APP_TAGLINE"],
        BRAND_RED=app.config["BRAND_RED"],
        ROLE_ADMIN=ROLE_ADMIN,
        ROLE_PROCAM_REP=ROLE_PROCAM_REP,
        ROLE_VENDOR_REP=ROLE_VENDOR_REP,
        ROLE_WORKER=ROLE_WORKER,
    )
    app.jinja_env.filters["incomma"] = indian_commas
    app.jinja_env.filters["inwords"] = amount_in_words

    # Blueprints
    from routes.auth import bp as auth_bp
    from routes.admin import bp as admin_bp
    from routes.vendor import bp as vendor_bp
    from routes.client import bp as client_bp
    from routes.attendance import bp as attendance_bp
    from routes.invoice import bp as invoice_bp
    from routes.api import bp as api_bp
    from routes.kiosk import bp as kiosk_bp
    from routes.payroll import bp as payroll_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(vendor_bp, url_prefix="/vendor")
    app.register_blueprint(client_bp, url_prefix="/client")
    app.register_blueprint(attendance_bp, url_prefix="/attendance")
    app.register_blueprint(invoice_bp, url_prefix="/invoice")
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(kiosk_bp)
    app.register_blueprint(payroll_bp)  # /payroll, /payroll/worker/<id>, /payroll/export.xlsx

    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        role = current_user.role
        # First-login force-chain (any role with must_change_password set)
        if getattr(current_user, "must_change_password", False):
            return redirect(url_for("auth.change_password"))
        if role == ROLE_ADMIN:
            return redirect(url_for("admin.dashboard"))
        if role == ROLE_PROCAM_REP:
            return redirect(url_for("client.dashboard"))
        if role == ROLE_VENDOR_REP:
            return redirect(url_for("vendor.dashboard"))
        if role == ROLE_GATE_GUARD:
            return redirect(url_for("kiosk.gate_screen"))
        # Workers: if no face template yet, force enrolment
        if role == ROLE_WORKER and current_user.worker_id:
            w = current_user.worker
            if w and not w.face_template:
                return redirect(url_for("attendance.enroll_face"))
        return redirect(url_for("attendance.worker_home"))

    # auto-create tables on first boot + ensure an HR admin exists.
    # This is idempotent — safe to run every time the worker starts.
    with app.app_context():
        db.create_all()
        _patch_schema()
        _ensure_admin()

    return app


def _ensure_admin():
    """Make sure at least one HR-admin user exists. Used on first deploy so the
       owner can sign in immediately. If admin already exists, no-op.

       Credentials come from env vars when set:
         ADMIN_USERNAME (default 'admin')
         ADMIN_PASSWORD (default 'admin123' — change on first login)
    """
    from models import User
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "admin123")
    if User.query.filter_by(username=username).first():
        return
    u = User(username=username, display_name="HR Admin",
             role=ROLE_ADMIN, is_active=True, must_change_password=True)
    u.set_password(password)
    db.session.add(u)
    try:
        db.session.commit()
        print(f"[bootstrap] admin user '{username}' created (must_change_password=True)")
    except Exception as e:
        db.session.rollback()
        print(f"[bootstrap] could not create admin: {e}")


def _patch_schema():
    """Lightweight at-boot schema patcher — adds new columns to existing tables
       so live DBs survive model changes without a wipe. No-op if everything is
       in sync. Mirrors the HRMS pattern.
    """
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db.engine)
    except Exception:
        return
    # Use TIMESTAMP on Postgres, DATETIME on SQLite — both work everywhere.
    dialect = db.engine.dialect.name
    DT = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"
    expected_columns = [
        # (table_name, column_name, DDL fragment — must be valid on both dialects)
        ("skills", "category", "VARCHAR(20) DEFAULT 'Unskilled' NOT NULL"),
        ("face_templates", "pose_count", "INTEGER DEFAULT 1 NOT NULL"),
        ("attendance", "punch_in_at",  DT),
        ("attendance", "punch_out_at", DT),
        ("agencies", "approver_mode", "VARCHAR(16) DEFAULT 'hr_only' NOT NULL"),
    ]
    for table, column, coldef in expected_columns:
        try:
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column in existing:
                continue
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}"))
                conn.commit()
            print(f"[schema] added {table}.{column}")
        except Exception as e:
            print(f"[schema] skipped {table}.{column}: {e}")


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=True)
