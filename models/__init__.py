"""SQLAlchemy models for Procam Attendance System."""
from datetime import datetime, date
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from passlib.hash import bcrypt as _bcrypt

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# User & roles
# ---------------------------------------------------------------------------
ROLE_ADMIN = "Admin"
ROLE_PROCAM_REP = "ProcamRep"
ROLE_VENDOR_REP = "VendorRep"
ROLE_WORKER = "Worker"
ROLE_GATE_GUARD = "GateGuard"   # mans the kiosk tablet at the gate
ALL_ROLES = (ROLE_ADMIN, ROLE_PROCAM_REP, ROLE_VENDOR_REP, ROLE_WORKER, ROLE_GATE_GUARD)

# ---------------------------------------------------------------------------
# Worker category — Minimum Wages Act classification. Stored on Skill; every
# worker carrying that skill inherits this category. Drives min-wage compliance,
# rate-card tiering and reporting.
# ---------------------------------------------------------------------------
CAT_UNSKILLED = "Unskilled"
CAT_SEMI_SKILLED = "Semi-Skilled"
CAT_SKILLED = "Skilled"
CAT_HIGHLY_SKILLED = "Highly Skilled"
WORKER_CATEGORIES = [CAT_UNSKILLED, CAT_SEMI_SKILLED, CAT_SKILLED, CAT_HIGHLY_SKILLED]


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120))
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(120))
    role = db.Column(db.String(32), nullable=False, default=ROLE_WORKER, index=True)

    agency_id = db.Column(db.Integer, db.ForeignKey("agencies.id"))
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"))

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    agency = db.relationship("Agency", foreign_keys=[agency_id])
    worker = db.relationship("Worker", foreign_keys=[worker_id])

    # ------- password helpers
    def set_password(self, raw: str):
        self.password_hash = _bcrypt.hash(raw)

    def check_password(self, raw: str) -> bool:
        try:
            return _bcrypt.verify(raw, self.password_hash)
        except ValueError:
            return False

    def __repr__(self):  # pragma: no cover
        return f"<User {self.username} ({self.role})>"


# ---------------------------------------------------------------------------
# Master tables
# ---------------------------------------------------------------------------
class Agency(db.Model):
    __tablename__ = "agencies"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    contact_person = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    address = db.Column(db.Text)
    gstin = db.Column(db.String(20))
    pan = db.Column(db.String(15))
    bank_name = db.Column(db.String(120))
    account_no = db.Column(db.String(40))
    ifsc = db.Column(db.String(20))
    default_tds_rate = db.Column(db.Numeric(5, 2), default=Decimal("2.00"))
    default_gst_rate = db.Column(db.Numeric(5, 2), default=Decimal("18.00"))
    onboarded_on = db.Column(db.Date, default=date.today)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Approval mode for attendance under this agency:
    #   'none'     → no approval required (PROCAM in-house — punch is the record)
    #   'hr_only'  → one approval from HR
    #   'dual'     → vendor rep + Procam rep (used for contract manpower / NPR)
    approver_mode = db.Column(db.String(16), default="none", nullable=False)


# Approver-mode constants
APPROVER_NONE    = "none"      # punch is the record; HR downloads daily Excel
APPROVER_HR_ONLY = "hr_only"
APPROVER_DUAL    = "dual"


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    client_name = db.Column(db.String(160))
    location = db.Column(db.String(160))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    procam_rep_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    procam_rep = db.relationship("User", foreign_keys=[procam_rep_id])


class Skill(db.Model):
    __tablename__ = "skills"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    # Minimum Wages Act category — Unskilled / Semi-Skilled / Skilled / Highly Skilled.
    # Defaults to Unskilled so legacy rows are valid.
    category = db.Column(db.String(20), default=CAT_UNSKILLED, nullable=False)


class RateCard(db.Model):
    __tablename__ = "rate_cards"

    id = db.Column(db.Integer, primary_key=True)
    agency_id = db.Column(db.Integer, db.ForeignKey("agencies.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"), nullable=False)
    daily_rate = db.Column(db.Numeric(10, 2), nullable=False)
    ot_hourly_rate = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    effective_from = db.Column(db.Date, nullable=False, default=date.today)
    effective_to = db.Column(db.Date)

    agency = db.relationship("Agency")
    project = db.relationship("Project")
    skill = db.relationship("Skill")

    __table_args__ = (
        db.UniqueConstraint("agency_id", "project_id", "skill_id", "effective_from",
                            name="uq_rate_card"),
    )


class Worker(db.Model):
    __tablename__ = "workers"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    agency_id = db.Column(db.Integer, db.ForeignKey("agencies.id"), nullable=False)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"))
    gender = db.Column(db.String(10))
    mobile = db.Column(db.String(20))
    aadhaar = db.Column(db.String(20))  # store full, mask in UI
    bank_name = db.Column(db.String(120))
    account_no = db.Column(db.String(40))
    ifsc = db.Column(db.String(20))
    photo_path = db.Column(db.String(255))
    onboarded_on = db.Column(db.Date, default=date.today)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Reporting line — captured from PRERNA's "Mgr Code" column.
    # Stored as the manager's emp_code (string) rather than a worker FK so the
    # importer can run before the manager's row exists, and so vendor workers
    # (no manager) can stay NULL. Approval routing reads this to find the
    # manager's User account for the first-level sign-off.
    manager_code = db.Column(db.String(32))
    designation  = db.Column(db.String(120))   # e.g. "Sr Manager", "Driver"
    vertical     = db.Column(db.String(80))    # e.g. "Warehouse", "Finance"
    grade        = db.Column(db.String(20))    # e.g. "M2", "J1"

    agency = db.relationship("Agency", backref="workers")
    skill = db.relationship("Skill")

    @property
    def category(self) -> str:
        """Min-Wages category inherited from the worker's skill."""
        return self.skill.category if self.skill else CAT_UNSKILLED

    @property
    def aadhaar_masked(self) -> str:
        if not self.aadhaar:
            return ""
        return "XXXX-XXXX-" + self.aadhaar[-4:]


class ProjectAssignment(db.Model):
    __tablename__ = "project_assignments"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    from_date = db.Column(db.Date, nullable=False, default=date.today)
    to_date = db.Column(db.Date)

    worker = db.relationship("Worker")
    project = db.relationship("Project")


class FaceTemplate(db.Model):
    """Stores 1 OR MORE 128-D face embeddings per worker.

    descriptor_json is a JSON document. To stay backward-compatible with the
    original single-descriptor rows, accept BOTH shapes:
      * [f0, f1, ... f127]                          (legacy single descriptor)
      * [[f0,...f127], [f0,...f127], ...]           (multi-pose list)
      * {"poses": {"Centre":[...], "Left":[...]}}   (new pose-tagged dict)
    The `references()` helper below normalises to a list-of-lists.
    """
    __tablename__ = "face_templates"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"),
                          unique=True, nullable=False)
    descriptor_json = db.Column(db.Text, nullable=False)
    enrolled_at = db.Column(db.DateTime, default=datetime.utcnow)
    # How many distinct pose captures went into this enrolment (1 = legacy).
    pose_count = db.Column(db.Integer, default=1, nullable=False)
    # Base64-encoded JPEG snapshot of the Centre pose. Stored in the DB so it
    # survives Render's ephemeral disk. Capped to ~200x200 to keep it small
    # (each photo ~12-20 KB → entire workforce well under 5 MB).
    snapshot_b64 = db.Column(db.Text)

    worker = db.relationship("Worker", backref=db.backref("face_template", uselist=False))

    def references(self) -> list[list[float]]:
        """Return the stored embeddings as a list of 128-float vectors.
        Handles all three historical shapes transparently."""
        import json
        try:
            data = json.loads(self.descriptor_json)
        except Exception:
            return []
        # legacy: flat 128-float vector
        if isinstance(data, list) and data and isinstance(data[0], (int, float)):
            return [list(data)]
        # list of vectors
        if isinstance(data, list) and data and isinstance(data[0], list):
            return [list(v) for v in data if len(v) == 128]
        # pose-tagged dict
        if isinstance(data, dict) and "poses" in data:
            return [list(v) for v in data["poses"].values() if len(v) == 128]
        return []


# ---------------------------------------------------------------------------
# Attendance + approvals
# ---------------------------------------------------------------------------
ATT_PRESENT = "Present"
ATT_ABSENT = "Absent"
ATT_HALF = "HalfDay"
ATT_HOLIDAY = "Holiday"
ATT_REGULARIZED = "Regularized"

# ---------------------------------------------------------------------------
# Attendance capture mode. Mode 1 (Kiosk) is the original gate flow and must
# stay the default so every legacy row and every future kiosk punch is tagged
# 'Kiosk' automatically. Mode 2 (Self) is the employee self-attendance feature.
# 'AdminManual' is reserved for a future admin-entered attendance mode.
# ---------------------------------------------------------------------------
ATT_TYPE_KIOSK = "Kiosk"
ATT_TYPE_SELF = "Self"
ATT_TYPE_ADMIN = "AdminManual"
ATTENDANCE_TYPES = (ATT_TYPE_KIOSK, ATT_TYPE_SELF, ATT_TYPE_ADMIN)

# Approval "sides". The original dual/hr flows use Vendor + Client. Employee
# self-attendance (Mode 2) is signed off by the employee's line Manager and
# then by HR — two extra side values on the same AttendanceApproval table.
SIDE_VENDOR = "Vendor"
SIDE_CLIENT = "Client"
SIDE_MANAGER = "Manager"
SIDE_HR = "HR"


class Attendance(db.Model):
    __tablename__ = "attendance"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False, index=True)
    hours = db.Column(db.Numeric(5, 2), default=Decimal("8.00"))
    status = db.Column(db.String(20), default=ATT_PRESENT, nullable=False)
    captured_at = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(20), default="Worker")  # Worker / VendorRep / Regularized
    # Distinct IN / OUT timestamps captured at gate (kiosk or worker self-punch).
    # When OUT is set, total_hours can later be computed from the gap.
    punch_in_at = db.Column(db.DateTime)
    punch_out_at = db.Column(db.DateTime)

    # -------------------------------------------------------------------
    # Mode 2 (Employee Self Attendance) fields.
    # All are nullable so existing kiosk rows remain valid untouched.
    # `attendance_type` defaults to 'Kiosk' so every legacy row and every
    # future kiosk punch is correctly classified without changing kiosk code.
    # -------------------------------------------------------------------
    attendance_type = db.Column(db.String(20), default=ATT_TYPE_KIOSK,
                                nullable=False, index=True)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gps_accuracy = db.Column(db.Float)          # metres reported by the browser
    location_name = db.Column(db.String(120))   # matched office name, if any
    distance_m = db.Column(db.Float)            # distance from the matched office
    face_verified = db.Column(db.Boolean)       # True once face passed threshold
    gps_verified = db.Column(db.Boolean)        # True once inside geofence
    outside_geofence = db.Column(db.Boolean, default=False)  # flagged, allowed by admin
    device = db.Column(db.String(160))          # parsed platform, e.g. "Android"
    browser = db.Column(db.String(160))         # parsed browser, e.g. "Chrome Mobile"
    ip_address = db.Column(db.String(64))
    self_photo_b64 = db.Column(db.Text)         # live JPEG captured at self-punch

    worker = db.relationship("Worker")
    project = db.relationship("Project")
    approvals = db.relationship("AttendanceApproval",
                                backref="attendance", cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("worker_id", "project_id", "work_date", name="uq_attendance"),
    )

    # convenience
    def approval(self, side: str):
        for a in self.approvals:
            if a.side == side:
                return a
        return None

    @property
    def is_dual_approved(self) -> bool:
        """Billable when approved per the worker's agency mode.
            - none    : punch IS the record — auto-approved on creation
            - hr_only : only the HR/Client side must be Approved
            - dual    : both Vendor and Client sides must be Approved
           Backwards-compatible: still called is_dual_approved everywhere.

           Employee Self Attendance (Mode 2) uses its own two-step chain that
           is independent of the agency mode: the employee's line Manager
           approves first, then HR gives the final sign-off. When an employee
           has no manager on file, only the HR approval is required."""
        # Mode 2 — self attendance: Manager (if any) + HR.
        if self.attendance_type == ATT_TYPE_SELF:
            mgr = self.approval(SIDE_MANAGER)
            hr = self.approval(SIDE_HR)
            mgr_ok = (mgr is None) or (mgr.status == "Approved")
            return bool(hr and hr.status == "Approved" and mgr_ok)

        mode = (self.worker.agency.approver_mode if self.worker and self.worker.agency
                else APPROVER_NONE)
        if mode == APPROVER_NONE:
            return True
        c = self.approval("Client")
        if mode == APPROVER_HR_ONLY:
            return bool(c and c.status == "Approved")
        v = self.approval("Vendor")
        return bool(v and c and v.status == "Approved" and c.status == "Approved")


class AttendanceApproval(db.Model):
    __tablename__ = "attendance_approvals"

    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(db.Integer, db.ForeignKey("attendance.id"), nullable=False)
    side = db.Column(db.String(10), nullable=False)        # Vendor | Client
    status = db.Column(db.String(12), default="Pending")   # Pending | Approved | Declined
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    remark = db.Column(db.Text)

    decided_by = db.relationship("User", foreign_keys=[decided_by_id])

    __table_args__ = (
        db.UniqueConstraint("attendance_id", "side", name="uq_attendance_approval"),
    )


class Regularization(db.Model):
    __tablename__ = "regularizations"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False)
    requested_status = db.Column(db.String(12), default=ATT_PRESENT)
    reason = db.Column(db.Text)
    status = db.Column(db.String(12), default="Pending")
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    decision_remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    worker = db.relationship("Worker")
    project = db.relationship("Project")
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(60), unique=True, nullable=False)
    agency_id = db.Column(db.Integer, db.ForeignKey("agencies.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    subtotal = db.Column(db.Numeric(14, 2), default=Decimal("0.00"))
    gst_rate = db.Column(db.Numeric(5, 2), default=Decimal("18.00"))
    gst_amount = db.Column(db.Numeric(14, 2), default=Decimal("0.00"))
    tds_rate = db.Column(db.Numeric(5, 2), default=Decimal("2.00"))
    tds_amount = db.Column(db.Numeric(14, 2), default=Decimal("0.00"))
    net_payable = db.Column(db.Numeric(14, 2), default=Decimal("0.00"))
    status = db.Column(db.String(12), default="Draft")  # Draft | Issued | Paid
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    agency = db.relationship("Agency")
    project = db.relationship("Project")
    generated_by = db.relationship("User")
    lines = db.relationship("InvoiceLine", backref="invoice",
                            cascade="all, delete-orphan")


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    skill = db.Column(db.String(80))
    days_present = db.Column(db.Integer, default=0)
    half_days = db.Column(db.Integer, default=0)
    total_billable_days = db.Column(db.Numeric(6, 2), default=Decimal("0.00"))
    daily_rate = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    line_amount = db.Column(db.Numeric(14, 2), default=Decimal("0.00"))

    worker = db.relationship("Worker")


# ---------------------------------------------------------------------------
# Mode 2 — Employee Self Attendance: offices, geofence assignment & settings.
# These are all NEW tables (created by db.create_all) — nothing existing is
# altered, so kiosk attendance is entirely unaffected.
# ---------------------------------------------------------------------------

# Many-to-many: an employee can be assigned to one OR more offices. Geofence
# validation runs against the employee's assigned offices (nearest wins). If an
# employee has no office assigned, validation falls back to ALL active offices.
worker_offices = db.Table(
    "worker_offices",
    db.Column("worker_id", db.Integer, db.ForeignKey("workers.id"),
              primary_key=True),
    db.Column("office_id", db.Integer, db.ForeignKey("offices.id"),
              primary_key=True),
)


class Office(db.Model):
    """A physical office/site with a geofence (centre + allowed radius)."""
    __tablename__ = "offices"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    city = db.Column(db.String(80))
    address = db.Column(db.Text)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    radius_m = db.Column(db.Integer, default=100, nullable=False)  # allowed radius
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # backref `.offices` is added onto Worker from this side.
    workers = db.relationship("Worker", secondary=worker_offices,
                              backref="offices")

    @property
    def is_geofence_ready(self) -> bool:
        return (self.latitude is not None and self.longitude is not None
                and self.radius_m and self.radius_m > 0)


class SelfAttendanceSettings(db.Model):
    """Singleton (id=1) holding every admin-configurable option for Mode 2.
       Read via `SelfAttendanceSettings.get()` which lazily creates the row
       with sane defaults so the feature works out of the box."""
    __tablename__ = "self_attendance_settings"

    id = db.Column(db.Integer, primary_key=True)
    enable_self_attendance = db.Column(db.Boolean, default=True, nullable=False)
    enable_face_verification = db.Column(db.Boolean, default=True, nullable=False)
    enable_gps = db.Column(db.Boolean, default=True, nullable=False)
    enable_geofence = db.Column(db.Boolean, default=True, nullable=False)
    allow_outside_radius = db.Column(db.Boolean, default=False, nullable=False)
    require_live_camera = db.Column(db.Boolean, default=True, nullable=False)
    capture_photo = db.Column(db.Boolean, default=True, nullable=False)
    # Reject GPS fixes whose reported accuracy is worse (larger) than this.
    max_gps_accuracy_m = db.Column(db.Integer, default=50, nullable=False)
    # Attendance time window (IST, "HH:MM"). Blank = no restriction.
    window_start = db.Column(db.String(5))   # e.g. "08:00"
    window_end = db.Column(db.String(5))     # e.g. "20:00"
    # Any IN punch after this clock time (IST, "HH:MM") is flagged Late.
    late_mark_after = db.Column(db.String(5))  # e.g. "09:30"
    # Anti-abuse: minimum seconds between two self punches by the same employee.
    cooldown_seconds = db.Column(db.Integer, default=60, nullable=False)
    # Fallback / default geofence used when NO office rows exist yet. Also used
    # to pre-fill the "new office" form. Offices table is authoritative.
    default_office_latitude = db.Column(db.Float)
    default_office_longitude = db.Column(db.Float)
    default_office_radius_m = db.Column(db.Integer, default=100)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @classmethod
    def get(cls) -> "SelfAttendanceSettings":
        s = cls.query.get(1)
        if not s:
            s = cls(id=1)
            db.session.add(s)
            db.session.commit()
        return s
