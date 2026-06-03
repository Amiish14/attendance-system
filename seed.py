"""One-shot seed for Procam Attendance.

Populates an admin, a Procam Rep, a demo agency, a vendor rep, a project,
three skills, three rate cards, three workers.
"""
from datetime import date
from decimal import Decimal

from app import create_app
from models import (
    db, User, Agency, Project, Skill, RateCard, Worker, ProjectAssignment,
    ROLE_ADMIN, ROLE_PROCAM_REP, ROLE_VENDOR_REP, ROLE_WORKER, ROLE_GATE_GUARD,
)


def _ensure_missing_users() -> list[str]:
    """Add demo users that don't exist yet — lets the seed be re-run safely
       when we add new roles (e.g. the gate kiosk guard) over time."""
    candidates = [
        # (username, password, role, display_name, extras)
        ("gate1", "gate1", ROLE_GATE_GUARD, "Gate 1 Security", {}),
    ]
    added = []
    for username, pw, role, display, extras in candidates:
        if User.query.filter_by(username=username).first():
            continue
        u = User(username=username, display_name=display, role=role,
                 is_active=True, must_change_password=False, **extras)
        u.set_password(pw)
        db.session.add(u)
        added.append(username)
    if added:
        db.session.commit()
    return added


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()

        # Idempotent top-up: if the demo set is already there but a new role-user
        # is missing (e.g. gate1 was added after this DB was first seeded), add
        # only the missing rows instead of bailing out.
        if User.query.filter_by(username="admin").first():
            added = _ensure_missing_users()
            if added:
                print(f"Top-up seed: added {len(added)} missing user(s) → {added}")
            else:
                print("Seed already applied — nothing to add.")
            return

        # ---- admin
        admin = User(
            username="admin", display_name="HR Admin",
            email="hr@procamlogistics.com",
            role=ROLE_ADMIN, is_active=True, must_change_password=False,
        )
        admin.set_password("admin123")
        db.session.add(admin)

        # ---- procam rep
        prep = User(
            username="procamrep", display_name="Procam Site Rep",
            role=ROLE_PROCAM_REP, is_active=True, must_change_password=False,
        )
        prep.set_password("procam123")
        db.session.add(prep)

        # ---- agency
        agency = Agency(
            code="VELO", name="Velocity Manpower Pvt Ltd",
            contact_person="Ramesh K", email="ops@velocityhr.in", phone="9988776655",
            address="A-12, Bhiwandi Industrial Estate, Thane",
            gstin="27AABCV1234X1Z5", pan="AABCV1234X",
            bank_name="HDFC Bank", account_no="50100123456789", ifsc="HDFC0000123",
            default_tds_rate=Decimal("2.00"),
            default_gst_rate=Decimal("18.00"),
            onboarded_on=date.today(), is_active=True,
        )
        db.session.add(agency)
        db.session.flush()

        # ---- vendor rep under that agency
        vrep = User(
            username="vendor1", display_name="Velocity Site Rep",
            role=ROLE_VENDOR_REP, agency_id=agency.id,
            is_active=True, must_change_password=False,
        )
        vrep.set_password("vendor123")
        db.session.add(vrep)

        # ---- gate guard who mans the kiosk
        guard = User(
            username="gate1", display_name="Gate 1 Security",
            role=ROLE_GATE_GUARD, is_active=True, must_change_password=False,
        )
        guard.set_password("gate1")
        db.session.add(guard)
        db.session.flush()

        # ---- project
        project = Project(
            code="BWN-WH", name="Bhiwandi Warehouse",
            client_name="MegaMart India Pvt Ltd",
            location="Bhiwandi, MH",
            start_date=date(2025, 1, 1), end_date=None,
            procam_rep_id=prep.id, is_active=True,
        )
        db.session.add(project)
        db.session.flush()

        # ---- skills (with Min-Wages Act categories)
        skill_loader = Skill(name="Loader", category="Unskilled")
        skill_helper = Skill(name="Helper", category="Semi-Skilled")
        skill_super = Skill(name="Supervisor", category="Highly Skilled")
        db.session.add_all([skill_loader, skill_helper, skill_super])
        db.session.flush()

        # ---- rate cards
        db.session.add_all([
            RateCard(agency_id=agency.id, project_id=project.id,
                     skill_id=skill_loader.id, daily_rate=Decimal("650.00"),
                     ot_hourly_rate=Decimal("85.00"), effective_from=date(2025, 1, 1)),
            RateCard(agency_id=agency.id, project_id=project.id,
                     skill_id=skill_helper.id, daily_rate=Decimal("550.00"),
                     ot_hourly_rate=Decimal("70.00"), effective_from=date(2025, 1, 1)),
            RateCard(agency_id=agency.id, project_id=project.id,
                     skill_id=skill_super.id, daily_rate=Decimal("950.00"),
                     ot_hourly_rate=Decimal("120.00"), effective_from=date(2025, 1, 1)),
        ])

        # ---- 3 workers with logins
        worker_data = [
            ("W001", "Suresh Yadav",   skill_loader.id, "9000000001"),
            ("W002", "Pintu Kumar",    skill_helper.id, "9000000002"),
            ("W003", "Mahesh Patil",   skill_super.id,  "9000000003"),
        ]
        for code, name, sid, mob in worker_data:
            w = Worker(
                code=code, full_name=name,
                agency_id=agency.id, skill_id=sid,
                gender="Male", mobile=mob,
                aadhaar=f"12345678{code[-1]}0{code[-1]}{code[-1]}",
                bank_name="SBI", account_no="3110000" + code[-1] * 4, ifsc="SBIN0001234",
                onboarded_on=date.today(), is_active=True,
            )
            db.session.add(w)
            db.session.flush()
            db.session.add(ProjectAssignment(
                worker_id=w.id, project_id=project.id, from_date=date.today()))
            u = User(
                username=code, display_name=name,
                role=ROLE_WORKER, worker_id=w.id, agency_id=agency.id,
                is_active=True, must_change_password=True,
            )
            u.set_password(code)
            db.session.add(u)

        db.session.commit()
        print("=== Seed complete ===")
        print(" admin     / admin123     (Procam HR / Admin)")
        print(" procamrep / procam123    (Procam Rep)")
        print(" vendor1   / vendor123    (Vendor Rep for Velocity Manpower)")
        print(" W001/W001, W002/W002, W003/W003 (Workers — must change password)")


if __name__ == "__main__":
    seed()
