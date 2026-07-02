# Employee Self Attendance (Attendance Mode 2)

This document describes the **Employee Self Attendance** feature added to the
PROCAM Attendance System. It is **purely additive** — the existing gate **Kiosk
(Mode 1) workflow is unchanged** and continues to work exactly as before.

---

## 1. Overview

| | Mode 1 — Kiosk | Mode 2 — Employee Self |
|---|---|---|
| Where | Gate tablet, manned by security | Employee's own phone / laptop, after login |
| Who | Field / gate workers (vendor & NPR agencies) | In-house office staff (PROCAM agency) |
| Identify | Camera scans, matches **all** enrolled faces | Verifies against the **logged-in employee's own** face |
| Location | — | GPS + geofence against assigned office(s) |
| Approval | Vendor + Client (per agency mode) | **Manager → HR** |
| Code | `routes/kiosk.py` (untouched) | `routes/self_attendance.py` (new) |

The flow: **Login → Dashboard → Mark Attendance → Camera → Face Recognition →
Location Verification → Attendance Recorded.**

---

## 2. Who can use it

Per HR policy, self attendance is for **office staff only**. Eligibility is:

> The feature is enabled globally **and** the employee belongs to the in-house
> **PROCAM** agency (`approver_mode = none`).

Field / gate workers (vendor & NPR agencies, dual-approval) are automatically
excluded and keep using the kiosk. The rule lives in
`utils.worker_can_self_attend()`, and a `can_self_attend` flag is injected into
every template so the **Mark Attendance** button appears only for eligible users.

---

## 3. Security & anti-abuse

All validation is enforced **server-side** in `routes/self_attendance.py`, so
browser dev-tools cannot bypass it. The client only ever sends a face
*descriptor* and an optional live JPEG — **there is no file-upload path**.

- **Live camera only** — reuses the existing `face.js` (`getUserMedia`); no file
  input, no gallery selection exists in the page.
- **Face verification** — the live descriptor is matched against the employee's
  **own** enrolled template only (`best_face_match`, same engine and
  `FACE_MATCH_THRESHOLD` as the kiosk). A mismatch records **nothing**.
- **GPS required** — permission-denied or no-fix is rejected.
- **Accuracy gate** — fixes worse than *Maximum GPS Accuracy* (default 50 m) are
  rejected.
- **Geofence** — distance to the nearest assigned office (Haversine) must be
  within the office radius, otherwise rejected (or flagged "Outside Office" if
  the admin allows it).
- **Cooldown** — a configurable minimum gap between two self punches blocks
  rapid-refresh / double-punch abuse.
- **Time window / late mark** — optional allowed window and a late-mark cutoff.
- **Audit trail** — every punch stores device, browser, IP, coordinates,
  accuracy, distance, and a live photo.

---

## 4. Approval chain

Self-attendance punches route **Manager → HR**:

1. The employee's line **Manager** (found via `worker.manager_code`) approves at
   **Team Self Attendance** (`/self/approvals`). ProcamRep managers see only
   their direct reports; Admin sees all.
2. **HR** gives the final sign-off at **Admin → Self Attendance → HR Approvals**
   (`/admin/self-attendance/approvals`).
3. Only then is the day billable (`Attendance.is_dual_approved`).

If an employee has **no manager on file**, only the HR approval is required.

---

## 5. Admin configuration

**Admin → Self Attendance** in the top nav:

- **Settings** (`/admin/self-attendance/settings`) — Enable Self Attendance,
  Enable Face Verification, Enable GPS, Enable Geofence, Allow Outside Radius,
  Require Live Camera, Capture Photo, Maximum GPS Accuracy, Cooldown, Attendance
  Time Window, Late-mark cutoff, and default-office coordinates.
- **Offices & Geofences** (`/admin/offices`) — create/edit offices, each with a
  latitude, longitude and allowed radius (metres). Four placeholder offices
  (Kolkata, Delhi, Mumbai, Dubai) are seeded on first boot; **set their real
  coordinates before rollout**. Assign employees to an office on its Edit page
  (an employee with no office assigned is validated against every active
  office — nearest wins).
- **HR Approvals** (`/admin/self-attendance/approvals`) — final sign-off queue.

> Tip for coordinates: open Google Maps, right-click the office, and copy the
> `lat, long` pair.

---

## 6. Reports

The **Admin → Daily attendance** page and its **Excel export** now include an
**Attendance Type** column plus, for self punches, a detail section with
**Face**, **GPS**, **Office**, **Distance**, **Accuracy**, **Map link**,
**Device**, **Browser** and **IP**. Kiosk rows are unchanged.

Employees see their own history (Date, Check In, Check Out, Type, Location,
Status) on the Mark Attendance page.

---

## 7. Database changes

New columns added to the existing **`attendance`** table (all nullable; the type
backfills to `'Kiosk'` so every legacy and future kiosk row is correct):

```
attendance_type, latitude, longitude, gps_accuracy, location_name, distance_m,
face_verified, gps_verified, outside_geofence, device, browser, ip_address,
self_photo_b64
```

New tables: **`offices`**, **`worker_offices`** (employee↔office M2M),
**`self_attendance_settings`** (singleton).

New `AttendanceApproval.side` values: **`Manager`**, **`HR`**.

### Migration

No manual step is required. The app self-migrates on boot:

- `db.create_all()` creates the new tables.
- `app._patch_schema()` adds the new `attendance` columns (idempotent, works on
  both SQLite and Postgres).
- `app._seed_self_attendance()` creates the settings row + placeholder offices.

Existing attendance records are **not modified** and remain fully readable.
(Backups already exist under `instance/attendance.db.backup-*`.)

---

## 8. Files changed / added

**Changed**
- `models/__init__.py` — new columns, `Office`, `SelfAttendanceSettings`,
  `worker_offices`, type/side constants, `is_dual_approved` handles Self.
- `app.py` — schema patch entries, seeding, blueprint registration, context
  processor.
- `utils.py` — `haversine_m`, `worker_can_self_attend`, `parse_user_agent`,
  `client_ip`.
- `routes/admin.py` — settings, offices CRUD + assignment, HR approvals; daily
  report + Excel export extended.
- `templates/_partials/topnav.html`, `templates/attendance/worker_home.html`,
  `templates/admin/attendance_daily.html`.

**Added**
- `routes/self_attendance.py`
- `templates/attendance/self_attendance.html`
- `templates/attendance/self_approvals.html`
- `templates/admin/self_attendance_settings.html`
- `templates/admin/offices.html`
- `templates/admin/office_form.html`
- `templates/admin/self_attendance_approvals.html`
- `tests/test_self_attendance.py`

---

## 9. Testing

`tests/test_self_attendance.py` runs against an isolated temporary SQLite DB and
covers both modes end-to-end — 28 checks, all passing:

```bash
python tests/test_self_attendance.py
```

It verifies: kiosk still records punches tagged `Kiosk`; field workers are
blocked from self attendance; face-pass + inside-geofence is accepted; face
mismatch / GPS denied / poor accuracy / outside geofence are rejected;
allow-outside flagging; cooldown; the Manager→HR approval chain; and that the
reports/exports render.
