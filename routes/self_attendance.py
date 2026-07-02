"""Employee Self Attendance — Attendance Mode 2.

Mode 1 (the gate kiosk, ``routes/kiosk.py``) is deliberately left untouched —
this blueprint is entirely additive. It lets an office employee mark their own
attendance after logging into the portal, secured by:

  * Face verification against THEIR OWN enrolled template (not identify-all —
    an employee can only ever mark *themselves*). Reuses the existing
    face-api.js descriptor engine + ``best_face_match`` — no second engine.
  * Live camera capture only. There is no file-upload path: the client sends a
    128-D descriptor (and an optional live JPEG), never an uploaded image.
  * GPS capture + geofence validation against the employee's assigned office(s).
  * Server-side anti-abuse — cooldown, accuracy gate, time-window, duplicate
    suppression — all enforced on the server so browser dev-tools cannot bypass.

Every punch is stored with ``attendance_type='Self'`` and routed through a
Manager -> HR approval chain (see ``Attendance.is_dual_approved``).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from flask import (Blueprint, render_template, request, jsonify, redirect,
                   url_for, flash, current_app, abort)
from flask_login import login_required, current_user

from models import (
    db, Worker, User, Office, Attendance, AttendanceApproval, FaceTemplate,
    SelfAttendanceSettings, ATT_PRESENT, ATT_TYPE_SELF, SIDE_MANAGER, SIDE_HR,
    ROLE_ADMIN, ROLE_PROCAM_REP,
)
from utils import (best_face_match, descriptor_loads, haversine_m,
                   worker_can_self_attend, parse_user_agent, client_ip,
                   to_ist, ist_now, fmt_ist_time)

# Reuse the kiosk's default-project helper so both modes write against the same
# "General Attendance" project — no duplicated business logic, kiosk untouched.
from routes.kiosk import _ensure_default_project

bp = Blueprint("self_att", __name__)


# ---------------------------------------------------------------------------
# Eligibility guard — office staff only, feature must be enabled.
# ---------------------------------------------------------------------------
def _settings() -> SelfAttendanceSettings:
    return SelfAttendanceSettings.get()


def self_att_eligible(fn):
    @wraps(fn)
    def inner(*a, **kw):
        s = _settings()
        worker = getattr(current_user, "worker", None)
        if not worker_can_self_attend(worker, s):
            # Wants JSON for the API endpoint, HTML for the page.
            if request.method == "POST" or request.path.endswith("/mark"):
                return jsonify(ok=False, reason="not_eligible",
                               message="Self attendance isn't available for your "
                                       "account."), 403
            flash("Self attendance isn't enabled for your account.", "warning")
            return redirect(url_for("index"))
        return fn(*a, **kw)
    return inner


def _candidate_offices(worker) -> list[Office]:
    """Offices to validate the GPS fix against. An employee's explicitly
       assigned offices win; if none are assigned we fall back to every active
       office that has coordinates configured (so the feature works before HR
       finishes assigning people)."""
    assigned = [o for o in (worker.offices or []) if o.is_active and o.is_geofence_ready]
    if assigned:
        return assigned
    return [o for o in Office.query.filter_by(is_active=True).all()
            if o.is_geofence_ready]


def _nearest_office(worker, lat: float, lon: float):
    """Return (office, distance_m) for the closest geofence-ready office, or
       (None, None) if there are no offices with coordinates to compare."""
    best_o, best_d = None, None
    for o in _candidate_offices(worker):
        d = haversine_m(lat, lon, o.latitude, o.longitude)
        if best_d is None or d < best_d:
            best_o, best_d = o, d
    return best_o, best_d


# ---------------------------------------------------------------------------
# Employee page
# ---------------------------------------------------------------------------
@bp.route("/attendance")
@login_required
@self_att_eligible
def page():
    """The 'Mark Attendance' dashboard page for the logged-in employee."""
    s = _settings()
    worker = current_user.worker
    today = date.today()
    today_att = (Attendance.query
                 .filter_by(worker_id=worker.id, work_date=today)
                 .order_by(Attendance.captured_at.desc())
                 .first())
    recent = (Attendance.query
              .filter_by(worker_id=worker.id)
              .order_by(Attendance.work_date.desc())
              .limit(14).all())
    offices = _candidate_offices(worker)
    return render_template("attendance/self_attendance.html",
                           settings=s, worker=worker, today_att=today_att,
                           recent=recent, offices=offices,
                           has_face=bool(worker.face_template))


# ---------------------------------------------------------------------------
# Mark attendance (JSON API called by the page)
# ---------------------------------------------------------------------------
@bp.route("/attendance/mark", methods=["POST"])
@login_required
@self_att_eligible
def mark():
    """Body (JSON):
         descriptor : [128 floats]         (live face descriptor)
         snapshot   : "data:image/jpeg..." (optional live JPEG)
         latitude, longitude, accuracy     (numbers)
         gps_error  : "denied"|"unavailable"|null
         kind       : "in"|"out"

       Returns JSON. On ANY validation failure NOTHING is recorded — the
       response explains why. Success returns the punch details for the UI.
    """
    s = _settings()
    worker = current_user.worker
    d = request.get_json(silent=True) or {}
    kind = (d.get("kind") or "").lower()
    if kind not in ("in", "out"):
        kind = "in"

    now = datetime.utcnow()

    # --- Anti-abuse: cooldown between two self punches (server-side) --------
    if s.cooldown_seconds and s.cooldown_seconds > 0:
        cutoff = now - timedelta(seconds=int(s.cooldown_seconds))
        recent = (Attendance.query
                  .filter(Attendance.worker_id == worker.id,
                          Attendance.attendance_type == ATT_TYPE_SELF,
                          Attendance.captured_at >= cutoff)
                  .first())
        if recent:
            wait = int(s.cooldown_seconds)
            return jsonify(ok=False, reason="cooldown",
                           message=(f"Please wait — you just marked attendance. "
                                    f"Try again in up to {wait}s.")), 429

    # --- Attendance time window (IST) --------------------------------------
    ist = ist_now()
    if s.window_start and s.window_end:
        try:
            hh1, mm1 = map(int, s.window_start.split(":"))
            hh2, mm2 = map(int, s.window_end.split(":"))
            start_min = hh1 * 60 + mm1
            end_min = hh2 * 60 + mm2
            cur_min = ist.hour * 60 + ist.minute
            if not (start_min <= cur_min <= end_min):
                return jsonify(ok=False, reason="outside_window",
                               message=(f"Self attendance is only allowed between "
                                        f"{s.window_start} and {s.window_end} IST.")), 403
        except ValueError:
            pass  # malformed setting — don't block attendance

    # --- Face verification (against the employee's OWN template) -----------
    face_verified = None
    similarity_pct = None
    if s.enable_face_verification:
        tpl = worker.face_template
        if not tpl:
            return jsonify(ok=False, reason="no_face",
                           message=("Your face isn't enrolled yet. Enrol your "
                                    "face once, then mark attendance."),
                           enroll_url=url_for("attendance.enroll_face")), 400
        given = d.get("descriptor") or []
        if isinstance(given, str):
            given = descriptor_loads(given)
        if not given or len(given) != 128:
            return jsonify(ok=False, reason="bad_descriptor",
                           message="No clear face captured. Face the camera and retry."), 400
        threshold = float(current_app.config.get("FACE_MATCH_THRESHOLD", 0.50))
        dist, _ = best_face_match(given, tpl.references())
        similarity_pct = max(0, int(round((1.0 - dist) * 100)))
        if dist > threshold:
            # Face mismatch — refuse and record nothing.
            return jsonify(ok=False, reason="face_mismatch",
                           similarity_pct=similarity_pct,
                           message=(f"Face verification failed — only {similarity_pct}% "
                                    f"match against your enrolled face. Attendance not "
                                    f"recorded.")), 403
        face_verified = True

    # --- GPS capture + accuracy gate ---------------------------------------
    lat = d.get("latitude")
    lon = d.get("longitude")
    accuracy = d.get("accuracy")
    gps_error = (d.get("gps_error") or "").lower()
    gps_verified = None
    location_name = None
    distance_m = None
    outside = False

    if s.enable_gps:
        if gps_error == "denied":
            return jsonify(ok=False, reason="gps_denied",
                           message=("Location permission denied. Allow location "
                                    "access to mark attendance.")), 403
        if gps_error == "unavailable" or lat is None or lon is None:
            return jsonify(ok=False, reason="gps_unavailable",
                           message="Could not get your location. Attendance rejected."), 400
        try:
            lat = float(lat); lon = float(lon)
        except (TypeError, ValueError):
            return jsonify(ok=False, reason="gps_unavailable",
                           message="Invalid location data. Attendance rejected."), 400
        # Accuracy gate — poor fixes are rejected (configurable).
        acc = None
        try:
            acc = float(accuracy) if accuracy is not None else None
        except (TypeError, ValueError):
            acc = None
        if s.max_gps_accuracy_m and acc is not None and acc > float(s.max_gps_accuracy_m):
            return jsonify(ok=False, reason="poor_accuracy",
                           accuracy=round(acc, 1), allowed=s.max_gps_accuracy_m,
                           message=(f"GPS accuracy too low ({round(acc)}m). Allowed "
                                    f"≤{s.max_gps_accuracy_m}m. Move to open sky and retry.")), 400
        accuracy = acc

        # --- Geofence validation -------------------------------------------
        if s.enable_geofence:
            office, dist = _nearest_office(worker, lat, lon)
            if office is None:
                # No office coordinates configured to validate against.
                if s.allow_outside_radius:
                    outside = True
                    gps_verified = False
                    location_name = "Unverified (no office set)"
                else:
                    return jsonify(ok=False, reason="no_office",
                                   message=("No office geofence is configured. Ask HR "
                                            "to set office coordinates.")), 400
            else:
                distance_m = round(dist, 1)
                location_name = office.name
                if dist <= office.radius_m:
                    gps_verified = True
                else:
                    # Outside the allowed radius.
                    if s.allow_outside_radius:
                        outside = True
                        gps_verified = False
                    else:
                        return jsonify(ok=False, reason="outside_geofence",
                                       office=office.name, distance_m=distance_m,
                                       radius_m=office.radius_m,
                                       message=(f"You're {round(dist)}m from {office.name} "
                                                f"(allowed {office.radius_m}m). Attendance "
                                                f"rejected.")), 403
        else:
            # GPS captured but geofence disabled — coordinates are informational.
            gps_verified = True

    # --- Photo (live JPEG only; capped) ------------------------------------
    photo_b64 = None
    if s.capture_photo:
        snap = (d.get("snapshot") or "").strip()
        if snap.startswith("data:image"):
            snap = snap.split(",", 1)[-1]
        if snap and len(snap) <= 120_000:
            photo_b64 = snap

    # --- Device / browser / IP audit ---------------------------------------
    device, browser = parse_user_agent(request.headers.get("User-Agent"))
    ip = client_ip()

    # --- Late-mark evaluation (informational) ------------------------------
    is_late = False
    if kind == "in" and s.late_mark_after:
        try:
            lh, lm = map(int, s.late_mark_after.split(":"))
            if (ist.hour * 60 + ist.minute) > (lh * 60 + lm):
                is_late = True
        except ValueError:
            pass

    # --- Create / update today's attendance row ----------------------------
    project = _ensure_default_project()
    today = date.today()
    att = Attendance.query.filter_by(
        worker_id=worker.id, project_id=project.id, work_date=today).first()
    is_new = att is None
    if is_new:
        att = Attendance(worker_id=worker.id, project_id=project.id,
                         work_date=today, status=ATT_PRESENT,
                         attendance_type=ATT_TYPE_SELF, hours=Decimal("8.00"))
        db.session.add(att)
        if kind == "in":
            att.punch_in_at = now
        else:
            att.punch_out_at = now
        db.session.flush()
    else:
        if kind == "in" and not att.punch_in_at:
            att.punch_in_at = now
        elif kind == "out":
            att.punch_out_at = now
            if att.punch_in_at:
                gap = (now - att.punch_in_at).total_seconds() / 3600.0
                if 0 < gap <= 12:
                    att.hours = Decimal(str(round(gap, 2)))

    # Common fields on every self punch.
    att.attendance_type = ATT_TYPE_SELF
    att.captured_at = now
    att.source = f"Self:{kind}" + (":late" if is_late else "")
    att.latitude = lat if s.enable_gps and lat is not None else att.latitude
    att.longitude = lon if s.enable_gps and lon is not None else att.longitude
    att.gps_accuracy = accuracy if s.enable_gps else att.gps_accuracy
    att.location_name = location_name or att.location_name
    att.distance_m = distance_m if distance_m is not None else att.distance_m
    att.face_verified = face_verified
    att.gps_verified = gps_verified
    att.outside_geofence = outside
    att.device = device
    att.browser = browser
    att.ip_address = ip
    if photo_b64:
        att.self_photo_b64 = photo_b64

    # --- Approval chain: Manager (if any) -> HR ----------------------------
    _ensure_self_approvals(att, worker)
    db.session.commit()

    verified_time = fmt_ist_time(att.punch_out_at if kind == "out" else att.punch_in_at) \
        or fmt_ist_time(now)
    return jsonify(
        ok=True, kind=kind, new_row=is_new, attendance_id=att.id,
        time=verified_time, is_late=is_late,
        face_verified=bool(face_verified) if face_verified is not None else None,
        gps_verified=bool(gps_verified) if gps_verified is not None else None,
        outside_geofence=outside,
        similarity_pct=similarity_pct,
        location_name=location_name, distance_m=distance_m,
        message=(f"Attendance marked ({'IN' if kind == 'in' else 'OUT'}) at {verified_time}."
                 + (" Flagged: outside office." if outside else "")),
    )


def _ensure_self_approvals(att: Attendance, worker: Worker):
    """Create the pending Manager + HR approval rows for a self-attendance
       punch (only the ones that don't already exist). When the employee has no
       manager on file we create only the HR approval so the punch isn't stuck."""
    have = {a.side for a in att.approvals}
    # Manager side — only if a line manager is on file.
    if SIDE_MANAGER not in have and (worker.manager_code or "").strip():
        db.session.add(AttendanceApproval(attendance_id=att.id, side=SIDE_MANAGER,
                                          status="Pending"))
    if SIDE_HR not in have:
        db.session.add(AttendanceApproval(attendance_id=att.id, side=SIDE_HR,
                                          status="Pending"))


# ---------------------------------------------------------------------------
# Manager approval queue — a manager signs off their direct reports' self
# punches (first step). ProcamRep managers + Admin can access.
# ---------------------------------------------------------------------------
@bp.route("/approvals", methods=["GET", "POST"])
@login_required
def manager_approvals():
    if current_user.role not in (ROLE_PROCAM_REP, ROLE_ADMIN):
        abort(403)

    # The manager's own emp code — used to find their direct reports.
    my_code = (current_user.worker.code if current_user.worker else None)

    if request.method == "POST":
        action = request.form.get("action")
        att_ids = request.form.getlist("att_id", type=int)
        new_status = "Approved" if action == "approve" else \
                     "Declined" if action == "decline" else None
        if new_status and att_ids:
            for aid in att_ids:
                att = Attendance.query.get(aid)
                if not att or att.attendance_type != ATT_TYPE_SELF:
                    continue
                # Authorisation — a manager may only decide their own reports;
                # Admin may decide anyone.
                if current_user.role != ROLE_ADMIN:
                    w = att.worker
                    if not (w and my_code and w.manager_code == my_code):
                        continue
                ap = att.approval(SIDE_MANAGER)
                if not ap:
                    ap = AttendanceApproval(attendance_id=att.id, side=SIDE_MANAGER)
                    db.session.add(ap)
                ap.status = new_status
                ap.decided_by_id = current_user.id
                ap.decided_at = datetime.utcnow()
            db.session.commit()
            flash(f"{len(att_ids)} self-attendance punch(es) {new_status.lower()}.", "success")
        return redirect(url_for("self_att.manager_approvals"))

    # Pending = Self punches whose Manager side is still Pending, for this
    # manager's direct reports (Admin sees all).
    q = (Attendance.query
         .join(Worker, Worker.id == Attendance.worker_id)
         .filter(Attendance.attendance_type == ATT_TYPE_SELF))
    if current_user.role != ROLE_ADMIN:
        q = q.filter(Worker.manager_code == my_code)
    rows = q.order_by(Attendance.captured_at.desc()).limit(200).all()
    pending, decided = [], []
    for a in rows:
        mgr = a.approval(SIDE_MANAGER)
        if mgr and mgr.status == "Pending":
            pending.append(a)
        elif mgr:
            decided.append(a)
    return render_template("attendance/self_approvals.html",
                           pending=pending, decided=decided[:40])
