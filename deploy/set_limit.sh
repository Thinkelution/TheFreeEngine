#!/usr/bin/env bash
# Set an account's daily character limit. Run with sudo.
#   set_limit.sh <email> unlimited | default | <number>
EMAIL=${1:?email}; VAL=${2:?unlimited|default|number}
sudo -u freeengine EMAIL="$EMAIL" VAL="$VAL" /opt/freeengine/venv/bin/python - <<'EOF'
import os, sqlite3
db = sqlite3.connect("/var/lib/freeengine/data/tfe.db")
email, val = os.environ["EMAIL"].lower(), os.environ["VAL"]
try:
    db.execute("ALTER TABLE users ADD COLUMN daily_chars INTEGER")
except sqlite3.OperationalError:
    pass
v = {"unlimited": 0, "default": None}.get(val, None if val == "default" else int(val) if val.isdigit() else "bad")
if v == "bad":
    raise SystemExit("value must be unlimited, default or a number")
row = db.execute("select id, verified, disabled from users where email=?", (email,)).fetchone()
if not row:
    raise SystemExit(f"no account for {email}")
with db:
    db.execute("update users set daily_chars=? where id=?", (v, row[0]))
r = db.execute("select email, verified, disabled, daily_chars from users where id=?", (row[0],)).fetchone()
print(f"{r[0]} verified={r[1]} disabled={r[2]} daily_chars={'unlimited' if r[3] == 0 else 'default' if r[3] is None else r[3]}")
EOF
