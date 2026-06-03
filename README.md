# PROCAM Attendance System

Standalone Flask app for manpower-vendor attendance + invoicing at Procam Logistics.

## Stack
Flask 3 · SQLAlchemy 3.1 · Flask-Login · Flask-Migrate · PyJWT · passlib[bcrypt] · openpyxl

## Quick start
```bash
cd "/Users/nileshsinha/Desktop/Attendance System"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py           # creates instance/attendance.db + demo data
python app.py            # serves http://localhost:5055
```

## Seed credentials

| Username   | Password   | Role        |
|------------|------------|-------------|
| admin      | admin123   | Admin (Procam HR) |
| procamrep  | procam123  | Procam Rep / approver |
| vendor1    | vendor123  | Vendor Rep (Velocity Manpower) |
| W001       | W001       | Worker (must change pwd on first login) |
| W002       | W002       | Worker |
| W003       | W003       | Worker |

## Roles
- **Admin** — manages agencies, projects, workers, rate cards, generates invoices, applies GST/TDS.
- **Procam Rep** — represents Procam/client at a project; approves the day's roster.
- **Vendor Rep** — represents an agency at a project; captures + confirms the day's roster.
- **Worker** — the labourer; face-punches in/out.

## Daily attendance flow
1. Worker face-punches at gate (camera + face descriptor + GPS).
2. Vendor Rep confirms roster at `/vendor/roster` — hours, half-day, no-shows.
3. Procam Rep approves at `/client/roster`.
4. Only dual-approved attendance becomes billable.

## Invoice math
- Subtotal = Σ (billable days × daily_rate from RateCard)
- GST = subtotal × gst_rate (default 18%)
- TDS = subtotal × tds_rate (default 2%) — deducted
- **Net = subtotal + GST − TDS**

## JWT API (`/api/v1`)
HS256, 12-hour token. Hit `POST /api/v1/auth/login` with `{username, password}`.
Use `Authorization: Bearer <token>` thereafter.
