"""Shared helpers: JWT, role decorators, number formatting, words, IST clock."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request, abort
from flask_login import current_user


# ---------------------------------------------------------------------------
# Indian Standard Time (UTC+5:30)
# ---------------------------------------------------------------------------
# Every datetime in the DB is stored as naive UTC (the SQLAlchemy default
# for `datetime.utcnow()`). The rest of the app expects naive datetimes too.
# These helpers convert UTC→IST for display, and provide a single Jinja
# filter so templates don't have to do timezone math each time.
IST = timezone(timedelta(hours=5, minutes=30))


def to_ist(dt: datetime | None) -> datetime | None:
    """Return a tz-aware IST datetime, or None if input is None.
    Treats naive datetimes as UTC (which is what we always store)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_now() -> datetime:
    """Tz-aware 'now' in IST. Useful when generating filenames or report headers."""
    return datetime.now(IST)


def fmt_ist(dt: datetime | None, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    """Default human format: '04 Jun 2026, 06:42 PM'. Pass a custom strftime
    string for other layouts. Returns '' for None — safe in templates."""
    ist = to_ist(dt)
    return "" if ist is None else ist.strftime(fmt) + " IST"


def fmt_ist_time(dt: datetime | None) -> str:
    """Just the clock part: '06:42 PM IST'. Used in kiosk feeds / punch logs."""
    ist = to_ist(dt)
    return "" if ist is None else ist.strftime("%I:%M %p") + " IST"


def fmt_ist_date(dt: datetime | None) -> str:
    """Date only: '04 Jun 2026'. No 'IST' suffix because dates aren't timezoned."""
    ist = to_ist(dt)
    return "" if ist is None else ist.strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def jwt_encode(user_id: int, role: str) -> tuple[str, datetime]:
    cfg = current_app.config
    exp = datetime.utcnow() + timedelta(hours=int(cfg["JWT_TTL_HOURS"]))
    token = jwt.encode(
        {"sub": user_id, "role": role, "exp": exp},
        cfg["JWT_SECRET"],
        algorithm=cfg["JWT_ALGO"],
    )
    return token, exp


def jwt_decode(token: str) -> dict | None:
    cfg = current_app.config
    try:
        return jwt.decode(token, cfg["JWT_SECRET"], algorithms=[cfg["JWT_ALGO"]])
    except jwt.PyJWTError:
        return None


def jwt_required(*roles):
    """API-style auth — reads Bearer token, sets g.api_user."""
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            from models import User  # late import
            auth = request.headers.get("Authorization", "")
            if not auth.lower().startswith("bearer "):
                return jsonify(error="missing bearer token"), 401
            payload = jwt_decode(auth.split(None, 1)[1].strip())
            if not payload:
                return jsonify(error="invalid or expired token"), 401
            user = User.query.get(payload["sub"])
            if not user or not user.is_active:
                return jsonify(error="user not active"), 401
            if roles and user.role not in roles:
                return jsonify(error="forbidden for role"), 403
            g.api_user = user
            return fn(*a, **kw)
        return inner
    return deco


# ---------------------------------------------------------------------------
# Web role decorator
# ---------------------------------------------------------------------------
def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not current_user.is_authenticated:
                return abort(401)
            if roles and current_user.role not in roles:
                return abort(403)
            return fn(*a, **kw)
        return inner
    return deco


# ---------------------------------------------------------------------------
# Indian number formatting + amount-in-words
# ---------------------------------------------------------------------------
def indian_commas(value) -> str:
    """1,75,156.00 style."""
    if value is None:
        return ""
    try:
        d = Decimal(value)
    except Exception:
        return str(value)
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole, _, frac = f"{d:.2f}".partition(".")
    # Indian grouping: last 3, then 2s
    if len(whole) <= 3:
        out = whole
    else:
        head, tail = whole[:-3], whole[-3:]
        # group head from right in 2s
        parts = []
        while len(head) > 2:
            parts.append(head[-2:])
            head = head[:-2]
        if head:
            parts.append(head)
        out = ",".join(reversed(parts)) + "," + tail
    return f"{sign}{out}.{frac}"


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten "
         "Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen "
         "Eighteen Nineteen").split()
_TENS = "Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t - 2] + ("" if o == 0 else " " + _ONES[o])


def _three_digit_words(n: int) -> str:
    h, rest = divmod(n, 100)
    out = ""
    if h:
        out += _ONES[h] + " Hundred"
        if rest:
            out += " "
    if rest:
        out += _two_digit_words(rest)
    return out


def amount_in_words(value) -> str:
    """Indian-style Rupees in words."""
    if value is None:
        return ""
    d = Decimal(value).quantize(Decimal("0.01"))
    whole = int(d)
    paise = int((d - whole) * 100)

    if whole == 0:
        rupees_words = "Zero"
    else:
        crore, rem = divmod(whole, 10000000)
        lakh, rem = divmod(rem, 100000)
        thousand, rem = divmod(rem, 1000)
        parts = []
        if crore:
            parts.append(_two_digit_words(crore) + " Crore")
        if lakh:
            parts.append(_two_digit_words(lakh) + " Lakh")
        if thousand:
            parts.append(_two_digit_words(thousand) + " Thousand")
        if rem:
            parts.append(_three_digit_words(rem))
        rupees_words = " ".join(parts)

    out = f"INR {rupees_words} Rupees"
    if paise:
        out += f" and {_two_digit_words(paise)} Paise"
    out += " Only"
    return out


# ---------------------------------------------------------------------------
# Face descriptor compare (squared Euclidean → sqrt → distance)
# ---------------------------------------------------------------------------
def face_distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 9.99
    s = 0.0
    for x, y in zip(a, b):
        s += (float(x) - float(y)) ** 2
    return s ** 0.5


def best_face_match(live: list[float], references: list[list[float]]):
    """Return (min_distance, index_of_best_reference). The live descriptor is
       compared against every stored reference and the closest wins — mirrors
       the multi-pose matching approach from the GitHub reference project."""
    if not references or not live:
        return 9.99, -1
    best_i = 0
    best_d = face_distance(live, references[0])
    for i in range(1, len(references)):
        d = face_distance(live, references[i])
        if d < best_d:
            best_d = d
            best_i = i
    return best_d, best_i


def descriptor_loads(s: str) -> list[float]:
    try:
        d = json.loads(s)
        if isinstance(d, list):
            return [float(x) for x in d]
    except Exception:
        pass
    return []


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Mode 2 — Employee Self Attendance helpers
# ---------------------------------------------------------------------------
import math


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/long points, in METRES.
       Accurate to well under a metre at city scale — plenty for geofencing."""
    R = 6371000.0  # Earth radius in metres
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def worker_can_self_attend(worker, settings) -> bool:
    """Eligibility gate for Employee Self Attendance (Mode 2).

       Policy (per HR): OFFICE STAFF ONLY — the in-house Procam employees whose
       agency runs in 'none' approver mode. Field / gate workers (vendor & NPR
       agencies, which use kiosk + dual approval) are excluded and keep using
       the kiosk exclusively. Gate-guard / system accounts have no Worker
       record and are therefore never eligible."""
    from models import APPROVER_NONE
    if settings is None or not settings.enable_self_attendance:
        return False
    if worker is None:
        return False
    ag = getattr(worker, "agency", None)
    if ag is None or ag.approver_mode != APPROVER_NONE:
        return False
    return True


def parse_user_agent(ua: str | None) -> tuple[str, str]:
    """Best-effort (device_platform, browser) from a User-Agent string.
       Deliberately dependency-free — good enough for the attendance audit
       trail without pulling in a UA-parsing library."""
    ua = ua or ""
    u = ua.lower()
    # Platform / device
    if "android" in u:
        device = "Android"
    elif "iphone" in u:
        device = "iPhone"
    elif "ipad" in u:
        device = "iPad"
    elif "windows" in u:
        device = "Windows"
    elif "mac os" in u or "macintosh" in u:
        device = "Mac"
    elif "linux" in u:
        device = "Linux"
    else:
        device = "Unknown device"
    # Browser (order matters — Edge/Chrome/Safari overlap in the UA string)
    if "edg/" in u or "edga/" in u:
        browser = "Edge"
    elif "opr/" in u or "opera" in u:
        browser = "Opera"
    elif "chrome" in u and "chromium" not in u:
        browser = "Chrome"
    elif "firefox" in u:
        browser = "Firefox"
    elif "safari" in u:
        browser = "Safari"
    else:
        browser = "Unknown browser"
    if "mobile" in u and browser != "Unknown browser":
        browser += " Mobile"
    return device[:160], browser[:160]


def client_ip() -> str:
    """Return the caller's IP, honouring a single X-Forwarded-For hop (Render
       / any reverse proxy puts the real client first in the list)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.remote_addr or "")[:64]
