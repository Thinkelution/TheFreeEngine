"""Manage API keys.  python keys.py create <name> | disable <key> | list"""
import datetime as dt
import secrets
import sys

from app import _db, key_hash

cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "create" and len(sys.argv) == 3:
    key = "tfe_" + secrets.token_urlsafe(24)
    with _db:
        _db.execute("INSERT INTO keys(hash, name, created) VALUES(?, ?, ?)",
                    (key_hash(key), sys.argv[2], dt.datetime.utcnow().isoformat()))
    print(key)
elif cmd == "disable" and len(sys.argv) == 3:
    with _db:
        n = _db.execute("UPDATE keys SET disabled=1 WHERE hash=?", (key_hash(sys.argv[2]),)).rowcount
    print("disabled" if n else "not found")
elif cmd == "list":
    for name, created, disabled in _db.execute("SELECT name, created, disabled FROM keys"):
        print(f"{name}\t{created}\t{'disabled' if disabled else 'active'}")
else:
    print(__doc__)
