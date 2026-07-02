"""End-to-end tests for Attendance Mode 2 (Employee Self Attendance) that also
prove Mode 1 (Kiosk) is unaffected.

Run:  python tests/test_self_attendance.py
Uses an isolated temporary SQLite DB — never touches instance/attendance.db.
"""
import os
import sys
import json
import tempfile

# Point the app at a throwaway DB BEFORE importing it.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["ADMIN_PASSWORD"] = "admin123"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                     # noqa: E402
from models import (db, User, Worker, Agency, FaceTemplate, Office,      # noqa: E402
                    SelfAttendanceSettings, Attendance, ATT_TYPE_SELF,
                    ATT_TYPE_KIOSK, APPROVER_NONE, APPROVER_DUAL,
                    ROLE_WORKER, ROLE_PROCAM_REP, ROLE_ADMIN, ROLE_GATE_GUARD,
                    SIDE_MANAGER, SIDE_HR)

app = create_app()

# Fixed descriptors (128-D). Identical -> distance 0 (pass); orthogonal -> big.
D_EMP = [0.0] * 128        # EMP1's enrolled face
D_FLD = [0.5] * 128        # FLD1's enrolled face (field worker, kiosk)
D_WRONG = [1.0] * 128      # clearly different -> face mismatch

# Kolkata office geofence
OFF_LAT, OFF_LON = 22.5726, 88.3639
FAR_LAT, FAR_LON = 23.5000, 88.0000   # ~110 km away

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {extra}" if extra and not cond else ""))


def _face_tpl(worker, desc):
    db.session.add(FaceTemplate(
        worker_id=worker.id,
        descriptor_json=json.dumps({"poses": {"Centre": desc}}),
        pose_count=1))


def seed():
    with app.app_context():
        procam = Agency.query.filter_by(code="PROCAM").first()
        if not procam:
            procam = Agency(code="PROCAM", name="Procam (in-house)",
                            approver_mode=APPROVER_NONE)
            db.session.add(procam)
        vendor = Agency(code="VND", name="Velocity Manpower",
                        approver_mode=APPROVER_DUAL)
        db.session.add(vendor); db.session.flush()

        # Manager (office, ProcamRep)
        mgr_w = Worker(code="MGR1", full_name="Meera Manager", agency_id=procam.id)
        db.session.add(mgr_w); db.session.flush()
        mgr_u = User(username="MGR1", display_name="Meera Manager",
                     role=ROLE_PROCAM_REP, worker_id=mgr_w.id,
                     agency_id=procam.id, is_active=True, must_change_password=False)
        mgr_u.set_password("mgr1")
        db.session.add(mgr_u)

        # Office employee reporting to MGR1
        emp_w = Worker(code="EMP1", full_name="Eshan Employee", agency_id=procam.id,
                       manager_code="MGR1", designation="Analyst")
        db.session.add(emp_w); db.session.flush()
        _face_tpl(emp_w, D_EMP)
        emp_u = User(username="EMP1", display_name="Eshan Employee",
                     role=ROLE_WORKER, worker_id=emp_w.id, agency_id=procam.id,
                     is_active=True, must_change_password=False)
        emp_u.set_password("emp1")
        db.session.add(emp_u)

        # A second office employee (no manager) to test cooldown in isolation
        emp2_w = Worker(code="EMP2", full_name="Nita NoManager", agency_id=procam.id)
        db.session.add(emp2_w); db.session.flush()
        _face_tpl(emp2_w, D_EMP)
        emp2_u = User(username="EMP2", display_name="Nita", role=ROLE_WORKER,
                      worker_id=emp2_w.id, agency_id=procam.id, is_active=True,
                      must_change_password=False)
        emp2_u.set_password("emp2")
        db.session.add(emp2_u)

        # Field worker (vendor agency) — kiosk only, NOT eligible for self
        fld_w = Worker(code="FLD1", full_name="Fahad Field", agency_id=vendor.id)
        db.session.add(fld_w); db.session.flush()
        _face_tpl(fld_w, D_FLD)
        fld_u = User(username="FLD1", display_name="Fahad Field", role=ROLE_WORKER,
                     worker_id=fld_w.id, agency_id=vendor.id, is_active=True,
                     must_change_password=False)
        fld_u.set_password("fld1")
        db.session.add(fld_u)

        # Kolkata office WITH coordinates (the seeded 4 have none).
        db.session.add(Office(code="KOLT", name="Kolkata Test Office",
                              city="Kolkata", latitude=OFF_LAT, longitude=OFF_LON,
                              radius_m=100, is_active=True))

        s = SelfAttendanceSettings.get()
        s.enable_self_attendance = True
        s.enable_face_verification = True
        s.enable_gps = True
        s.enable_geofence = True
        s.allow_outside_radius = False
        s.max_gps_accuracy_m = 50
        s.cooldown_seconds = 0
        db.session.commit()


def login(client, u, p):
    return client.post("/login", data={"username": u, "password": p},
                       follow_redirects=True)


def mark(client, **body):
    return client.post("/self/attendance/mark", json=body)


def run():
    seed()

    # ---- Mode 1 (Kiosk) still works and tags rows 'Kiosk' -----------------
    print("\nMode 1 — Kiosk (must remain unaffected):")
    with app.app_context():
        guard = User(username="gate9", role=ROLE_GATE_GUARD, is_active=True,
                     must_change_password=False)
        guard.set_password("gate9"); db.session.add(guard); db.session.commit()
    kc = app.test_client()
    login(kc, "gate9", "gate9")
    r = kc.post("/kiosk/identify", json={"descriptor": D_FLD, "kind": "in"})
    j = r.get_json()
    check("kiosk identify records a punch", r.status_code == 200 and j.get("ok"), str(j))
    with app.app_context():
        row = (Attendance.query.join(Worker)
               .filter(Worker.code == "FLD1").first())
        check("kiosk row tagged attendance_type='Kiosk'",
              row is not None and row.attendance_type == ATT_TYPE_KIOSK,
              row.attendance_type if row else "no row")

    # ---- Eligibility: field worker cannot self-attend ---------------------
    print("\nEligibility:")
    fc = app.test_client(); login(fc, "FLD1", "fld1")
    r = mark(fc, kind="in", descriptor=D_FLD, latitude=OFF_LAT,
             longitude=OFF_LON, accuracy=10)
    check("field worker blocked from self attendance (403)", r.status_code == 403,
          f"got {r.status_code}")
    r = fc.get("/self/attendance")
    check("field worker redirected away from self page", r.status_code in (302, 303),
          f"got {r.status_code}")

    # ---- Mode 2 happy path: face + inside geofence ------------------------
    print("\nMode 2 — Employee Self Attendance:")
    ec = app.test_client(); login(ec, "EMP1", "emp1")
    check("self page renders for eligible employee", ec.get("/self/attendance").status_code == 200)

    r = mark(ec, kind="in", descriptor=D_EMP, latitude=OFF_LAT, longitude=OFF_LON,
             accuracy=15, snapshot="data:image/jpeg;base64,AAAA")
    j = r.get_json()
    check("IN accepted (face+GPS ok)", r.status_code == 200 and j.get("ok"), str(j))
    check("face_verified true in response", j.get("face_verified") is True)
    check("gps_verified true in response", j.get("gps_verified") is True)
    with app.app_context():
        row = (Attendance.query.join(Worker).filter(Worker.code == "EMP1").first())
        check("self row tagged 'Self'", row and row.attendance_type == ATT_TYPE_SELF)
        check("office matched by geofence", row and row.location_name == "Kolkata Test Office")
        check("device/browser/IP captured", row and row.ip_address is not None)
        check("Manager + HR approvals created",
              row and {a.side for a in row.approvals} == {SIDE_MANAGER, SIDE_HR})
        check("not billable until approved", row and not row.is_dual_approved)

    # ---- Face mismatch is rejected, records nothing new -------------------
    with app.app_context():
        before = Attendance.query.count()
    ec2 = app.test_client(); login(ec2, "EMP2", "emp2")
    r = mark(ec2, kind="in", descriptor=D_WRONG, latitude=OFF_LAT,
             longitude=OFF_LON, accuracy=15)
    j = r.get_json()
    check("face mismatch rejected (403)", r.status_code == 403 and j.get("reason") == "face_mismatch",
          str(j))
    with app.app_context():
        check("no row created on face mismatch", Attendance.query.count() == before)

    # ---- GPS denied / poor accuracy --------------------------------------
    r = mark(ec2, kind="in", descriptor=D_EMP, gps_error="denied")
    check("GPS permission denied rejected", r.status_code == 403 and
          r.get_json().get("reason") == "gps_denied")
    r = mark(ec2, kind="in", descriptor=D_EMP, latitude=OFF_LAT, longitude=OFF_LON,
             accuracy=65)
    check("poor GPS accuracy rejected", r.status_code == 400 and
          r.get_json().get("reason") == "poor_accuracy")

    # ---- Outside geofence: reject, then allow-with-flag -------------------
    r = mark(ec2, kind="in", descriptor=D_EMP, latitude=FAR_LAT, longitude=FAR_LON,
             accuracy=15)
    check("outside geofence rejected when not allowed", r.status_code == 403 and
          r.get_json().get("reason") == "outside_geofence", str(r.get_json()))
    with app.app_context():
        s = SelfAttendanceSettings.get(); s.allow_outside_radius = True; db.session.commit()
    r = mark(ec2, kind="in", descriptor=D_EMP, latitude=FAR_LAT, longitude=FAR_LON,
             accuracy=15)
    j = r.get_json()
    check("outside geofence allowed-with-flag when enabled",
          r.status_code == 200 and j.get("ok") and j.get("outside_geofence") is True, str(j))

    # ---- Cooldown / rapid-refresh abuse ----------------------------------
    with app.app_context():
        s = SelfAttendanceSettings.get(); s.cooldown_seconds = 60; db.session.commit()
    r = mark(ec2, kind="out", descriptor=D_EMP, latitude=FAR_LAT, longitude=FAR_LON,
             accuracy=15)
    check("cooldown blocks rapid second punch (429)", r.status_code == 429 and
          r.get_json().get("reason") == "cooldown", str(r.get_json()))
    with app.app_context():
        s = SelfAttendanceSettings.get(); s.cooldown_seconds = 0; db.session.commit()

    # ---- Approval chain: Manager -> HR -> billable -----------------------
    print("\nApproval chain (Manager -> HR):")
    with app.app_context():
        row = Attendance.query.join(Worker).filter(Worker.code == "EMP1").first()
        aid = row.id
    mc = app.test_client(); login(mc, "MGR1", "mgr1")
    mc.post("/self/approvals", data={"action": "approve", "att_id": aid})
    with app.app_context():
        row = db.session.get(Attendance, aid)
        check("manager approval recorded", row.approval(SIDE_MANAGER).status == "Approved")
        check("still not billable (HR pending)", not row.is_dual_approved)
    adm = app.test_client(); login(adm, "admin", "admin123")
    adm.post("/admin/self-attendance/approvals", data={"action": "approve", "att_id": aid})
    with app.app_context():
        row = db.session.get(Attendance, aid)
        check("HR approval recorded", row.approval(SIDE_HR).status == "Approved")
        check("now billable (Manager+HR approved)", row.is_dual_approved)

    # ---- Reports export doesn't error ------------------------------------
    print("\nReports:")
    import datetime as _dt
    today = _dt.date.today().isoformat()
    r = adm.get(f"/admin/attendance/daily?date={today}")
    check("daily attendance page renders", r.status_code == 200)
    r = adm.get(f"/admin/attendance/daily.xlsx?date={today}")
    check("xlsx export downloads", r.status_code == 200 and
          r.headers.get("Content-Type", "").startswith(
              "application/vnd.openxmlformats"), r.status_code)
    r = adm.get("/admin/self-attendance/settings")
    check("settings panel renders", r.status_code == 200)
    r = adm.get("/admin/offices")
    check("offices page renders", r.status_code == 200)

    # ---- Summary ----------------------------------------------------------
    ok = sum(results); total = len(results)
    print(f"\n{'='*46}\n  {ok}/{total} checks passed\n{'='*46}")
    return ok == total


if __name__ == "__main__":
    try:
        success = run()
    finally:
        try:
            os.unlink(_TMP_DB)
        except OSError:
            pass
    sys.exit(0 if success else 1)
