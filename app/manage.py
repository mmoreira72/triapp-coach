"""CLI: python -m app.manage create-user EMAIL NAME coach|athlete [ATHLETE] ; set-password EMAIL ; list-users ; run-pipeline"""

import sys, getpass
from . import auth, config, pipeline


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    if cmd == "create-user":
        email, name, role = args[0], args[1], args[2]
        athlete = args[3] if len(args) > 3 else None
        if role == "athlete" and athlete not in config.athletes():
            print("athlete must be one of", list(config.athletes()))
            return 1
        pw = getpass.getpass("password: ")
        auth.upsert_user(email.lower(), name, role, athlete, pw)
        print("saved", email)
    elif cmd == "set-password":
        u = next((x for x in auth.list_users() if x["email"] == args[0].lower()), None)
        if not u:
            print("no such user")
            return 1
        auth.set_password(u["id"], getpass.getpass("new password: "))
        print("updated")
    elif cmd == "list-users":
        for u in auth.list_users():
            print(f"{u['id']:>3} {u['role']:<8} {u['email']:<35} {u['name']}  {u['athlete'] or ''}")
    elif cmd == "run-pipeline":
        pipeline._job(skip_drive="--skip-drive" in args, who="cli")
        print(pipeline.status())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
