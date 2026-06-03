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

    # One-shot recovery endpoints — only live when SETUP_TOKEN env var is set
    _register_setup_routes(app)

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
    existing = User.query.filter_by(username=username).first()
    if existing:
        print(f"[bootstrap] admin user '{username}' already exists "
              f"(id={existing.id}, active={existing.is_active}, "
              f"hash_len={len(existing.password_hash or '')})")
        return
    u = User(username=username, display_name="HR Admin",
             role=ROLE_ADMIN, is_active=True, must_change_password=True)
    u.set_password(password)
    print(f"[bootstrap] hashing test: set→verify={u.check_password(password)}")
    db.session.add(u)
    try:
        db.session.commit()
        print(f"[bootstrap] admin user '{username}' created (must_change_password=True)")
    except Exception as e:
        db.session.rollback()
        print(f"[bootstrap] could not create admin: {type(e).__name__}: {e}")


def _register_setup_routes(app):
    """One-shot recovery endpoints guarded by SETUP_TOKEN env var. Hit:
         /setup/health?token=<SETUP_TOKEN>
       to see whether admin exists, and
         /setup/reset-admin?token=<SETUP_TOKEN>&password=<new>
       to forcibly create-or-reset the admin user. Remove SETUP_TOKEN from
       Render's env vars once you're done — that disables these routes.
    """
    from flask import jsonify, request
    from models import User

    def _guard():
        # Read SETUP_TOKEN FRESH on every request (not captured at boot) so an
        # env-var change takes effect even if Render re-uses the worker process.
        # Strip whitespace from both sides — Render's UI sometimes preserves a
        # trailing newline / space when the value is pasted from a password
        # manager, which silently breaks exact-match comparison.
        expected = (os.environ.get("SETUP_TOKEN", "") or "").strip()
        token = (request.args.get("token") or "").strip()
        if not expected:
            return jsonify(error="setup routes disabled — set SETUP_TOKEN env var"), 403
        if token != expected:
            # Show lengths (not the values) so we can see whether there's a
            # hidden trailing character on either side.
            return jsonify(
                error="bad token",
                got_len=len(token),
                expected_len=len(expected),
                hint=("lengths differ — likely a hidden whitespace char in env var"
                      if len(token) != len(expected)
                      else "same length but value differs — env var content wrong"),
            ), 403
        return None

    @app.route("/setup/health")
    def setup_health():
        guard = _guard()
        if guard: return guard
        admin_username = os.environ.get("ADMIN_USERNAME", "admin")
        u = User.query.filter_by(username=admin_username).first()
        info = {
            "db_uri_kind": app.config["SQLALCHEMY_DATABASE_URI"].split("://", 1)[0],
            "total_users": User.query.count(),
            "looking_for": admin_username,
            "admin_exists": bool(u),
        }
        if u:
            info.update({
                "id": u.id, "is_active": u.is_active,
                "must_change_password": u.must_change_password,
                "role": u.role,
                "hash_length": len(u.password_hash or ""),
                "hash_prefix": (u.password_hash or "")[:7],
            })
        return jsonify(info)

    @app.route("/setup/reset-all-workers")
    def setup_reset_all_workers():
        """Reset every Worker user's password back to their username
           (i.e. emp_code) and force a new-password reset on next login.
           Optional ?clear_faces=1 also wipes their face templates so they
           must re-enrol from scratch.
        """
        from models import ROLE_WORKER as RW, FaceTemplate
        guard = _guard()
        if guard: return guard
        clear_faces = request.args.get("clear_faces") in ("1", "true", "yes")
        workers = User.query.filter_by(role=RW).all()
        reset_n = 0
        for u in workers:
            u.set_password(u.username)       # pw == emp_code (PRERNA convention)
            u.must_change_password = True
            u.is_active = True
            reset_n += 1
        faces_wiped = 0
        if clear_faces:
            faces_wiped = FaceTemplate.query.delete()
        db.session.commit()
        return jsonify(
            ok=True, role_reset="Worker",
            users_reset=reset_n,
            face_templates_wiped=faces_wiped,
            note=("Every worker: username=pw=emp_code; will be forced to change "
                  "password on next login. " +
                  ("Faces wiped — they must re-enrol." if clear_faces else
                   "Existing face enrolments preserved."))
        )

    @app.route("/setup/reset-admin")
    def setup_reset_admin():
        guard = _guard()
        if guard: return guard
        from models import ROLE_ADMIN as RA
        username = os.environ.get("ADMIN_USERNAME", "admin")
        new_pw = request.args.get("password") or "admin123"
        u = User.query.filter_by(username=username).first()
        created = False
        if not u:
            u = User(username=username, display_name="HR Admin",
                     role=RA, is_active=True, must_change_password=True)
            db.session.add(u); created = True
        u.set_password(new_pw)
        u.is_active = True
        u.must_change_password = True
        db.session.commit()
        # Verify the hash works
        ok = u.check_password(new_pw)
        return jsonify(
            created=created, username=username,
            password_set=True, verify_after_set=ok,
            hash_prefix=u.password_hash[:7],
            message=("CREATED" if created else "RESET") + f" — sign in as '{username}' with the password you supplied.",
        )


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
