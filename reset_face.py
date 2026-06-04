"""Reset face enrolment for one or more employees.

   Wipes the FaceTemplate row(s) so the affected user is forced into face
   enrolment again on their next login. Doesn't touch the user's password,
   role, or attendance history — only the stored face descriptor.

   Usage:
       python reset_face.py EMP_CODE [EMP_CODE ...]
       python reset_face.py --all                # wipe every face (nuclear option)

   Examples:
       # Just one person — useful when you tested by enrolling YOUR face for
       # someone else and want them to re-do it properly
       python reset_face.py EMP1552018

       # Multiple people in one shot
       python reset_face.py EMP1552018 EMP3892025 DIR12010

       # Reset everyone (admin, manager, worker — all of them)
       python reset_face.py --all
"""
import sys
from app import create_app
from models import db, User, Worker, FaceTemplate


def reset_for_codes(codes: list):
    app = create_app()
    with app.app_context():
        wiped = 0
        not_found = []
        no_face = []
        for code in codes:
            code = code.strip()
            w = Worker.query.filter_by(code=code).first()
            if not w:
                not_found.append(code)
                continue
            tpl = FaceTemplate.query.filter_by(worker_id=w.id).first()
            if not tpl:
                no_face.append(f"{code} ({w.full_name})")
                continue
            db.session.delete(tpl)
            wiped += 1
            print(f"  ✓ wiped face for {code} — {w.full_name}")
        db.session.commit()

        print(f"\nResult: {wiped} face template(s) wiped.")
        if no_face:
            print(f"No face was registered for: {', '.join(no_face)}")
        if not_found:
            print(f"Worker not found: {', '.join(not_found)}")
        if wiped:
            print(f"\nNext login for those user(s) → forced back into face enrolment.")


def reset_all():
    app = create_app()
    with app.app_context():
        n = FaceTemplate.query.count()
        print(f"About to wipe ALL {n} face templates in the database.")
        confirm = input("Type 'YES' to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            return
        deleted = FaceTemplate.query.delete()
        db.session.commit()
        print(f"\n✓ Wiped {deleted} face templates. "
              f"Everyone re-enrols on next login.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    if args[0] == "--all":
        reset_all()
    else:
        reset_for_codes(args)
