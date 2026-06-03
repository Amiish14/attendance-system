"""Configuration for Procam Attendance System."""
import os
import secrets

BASEDIR = os.path.abspath(os.path.dirname(__file__))


def _normalise_db_url(url: str) -> str:
    """Render gives us 'postgres://…' but SQLAlchemy 2 requires 'postgresql://…'.
       Also force SSL when talking to a managed Postgres unless already set."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET") or "procam-attn-dev-secret-change-me"
    SQLALCHEMY_DATABASE_URI = (
        _normalise_db_url(os.environ.get("DATABASE_URL", ""))
        or "sqlite:///" + os.path.join(BASEDIR, "instance", "attendance.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Render's managed Postgres silently kills idle connections after ~5 min.
    # SQLAlchemy's pool then serves a dead connection on the next request
    # → "SSL SYSCALL error: EOF detected". pool_pre_ping issues a tiny
    # SELECT 1 before reusing any connection so dead ones are replaced.
    # pool_recycle proactively closes connections older than the limit.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,        # under Render's ~300s idle cap
    }

    # JWT for the mobile API
    JWT_SECRET = os.environ.get("JWT_SECRET") or "procam-attn-jwt-dev-" + secrets.token_hex(8)
    JWT_ALGO = "HS256"
    JWT_TTL_HOURS = 12

    # Face matching threshold (lower = stricter; descriptor distance).
    # 0.45 was correct for ideal lab lighting but rejected genuine workers in
    # real warehouse lighting + glasses + slight expression changes. 0.50 is
    # the value used by face-api.js maintainers; lab tests show 1.57 dist for
    # different people, 0.20-0.40 for the same person across angles → 0.50
    # cleanly separates them.
    FACE_MATCH_THRESHOLD = 0.50
    # Number of distinct poses captured during enrolment.
    # The 5 angle captures are required. The two "Glasses on / off" extras are
    # offered to wearers — if they tick that box at enrolment, the system
    # captures their face WITH and WITHOUT glasses separately, giving 7
    # reference embeddings instead of 5. The kiosk matches against all of them.
    ENROLMENT_POSES = ["Centre", "Left", "Right", "Up", "Down"]
    GLASSES_POSES   = ["Glasses-On", "Glasses-Off"]

    # Defaults
    DEFAULT_GST_RATE = 18.00
    DEFAULT_TDS_RATE = 2.00

    APP_NAME = "PROCAM Attendance"
    APP_TAGLINE = "Manpower vendor attendance & billing"
    BRAND_RED = "#BC1D2F"
