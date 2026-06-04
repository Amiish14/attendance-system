"""Quick role override — set one or more users to a specific role.

   Usage:
       python set_role.py <ROLE> EMP_CODE [EMP_CODE ...]
       python set_role.py --list           # show every Admin / ProcamRep account

   Roles you can set (case-insensitive): admin | procamrep | worker | gateguard

   Examples:
       # demote three directors from Admin to ProcamRep
       python set_role.py procamrep DIR22010 DIR52011 DIR72012
       # promote someone to admin
       python set_role.py admin EMP3892025
       # see who currently has elevated access
       python set_role.py --list
"""
import sys
from app import create_app
from models import (db, User, ROLE_ADMIN, ROLE_PROCAM_REP,
                    ROLE_WORKER, ROLE_GATE_GUARD)

ROLE_ALIASES = {
    "admin":      ROLE_ADMIN,
    "hr":         ROLE_ADMIN,
    "procamrep":  ROLE_PROCAM_REP,
    "procam":     ROLE_PROCAM_REP,
    "rep":        ROLE_PROCAM_REP,
    "manager":    ROLE_PROCAM_REP,
    "worker":     ROLE_WORKER,
    "employee":   ROLE_WORKER,
    "gateguard":  ROLE_GATE_GUARD,
    "guard":      ROLE_GATE_GUARD,
    "gate":       ROLE_GATE_GUARD,
}


def _list_elevated():
    app = create_app()
    with app.app_context():
        admins = User.query.filter_by(role=ROLE_ADMIN).all()
        reps   = User.query.filter_by(role=ROLE_PROCAM_REP).all()
        guards = User.query.filter_by(role=ROLE_GATE_GUARD).all()
        print(f"\n--- Admins ({len(admins)}) — full HR access ---")
        for u in sorted(admins, key=lambda x: x.username):
            print(f"  {u.username:14s} {u.display_name or ''}")
        print(f"\n--- ProcamRep ({len(reps)}) — managers, approve their own team ---")
        for u in sorted(reps, key=lambda x: x.username):
            print(f"  {u.username:14s} {u.display_name or ''}")
        if guards:
            print(f"\n--- GateGuard ({len(guards)}) — kiosk operators ---")
            for u in sorted(guards, key=lambda x: x.username):
                print(f"  {u.username:14s} {u.display_name or ''}")
        print()


def set_role(role_input: str, emp_codes: list):
    target = ROLE_ALIASES.get(role_input.strip().lower())
    if not target:
        sys.exit(f"Unknown role '{role_input}'. Use: admin | procamrep | worker | gateguard")

    app = create_app()
    with app.app_context():
        changed = unchanged = missing = 0
        for code in emp_codes:
            code = code.strip()
            u = User.query.filter_by(username=code).first()
            if not u:
                print(f"  ✗ {code} — no such user")
                missing += 1
                continue
            if u.role == target:
                print(f"  · {code} — already {target} (no change)")
                unchanged += 1
                continue
            print(f"  ✓ {code} {u.display_name or '':30s}  {u.role} → {target}")
            u.role = target
            changed += 1
        db.session.commit()
        print(f"\nResult: {changed} changed · {unchanged} unchanged · {missing} not found\n")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        _list_elevated()
    elif len(sys.argv) >= 3:
        set_role(sys.argv[1], sys.argv[2:])
    else:
        sys.exit(__doc__)
