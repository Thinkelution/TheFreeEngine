#!/usr/bin/env bash
# Add an API key to an existing account. Usage: sudo bash add_key.sh <email> <key name>   (key on stdin)
# With no arguments: list accounts.
DB=/var/lib/freeengine/data/tfe.db
PY=/opt/freeengine/venv/bin/python
if [ -z "${1:-}" ]; then
  $PY -c "import sqlite3;[print(*r) for r in sqlite3.connect('$DB').execute('select id,email,verified,disabled from users')]"
  exit 0
fi
IFS= read -r KEY; KEY=${KEY%$'\r'}
sudo -u freeengine KEY="$KEY" EMAIL="$1" NAME="${2:-My key}" $PY - <<'EOF'
import datetime as dt, hashlib, os, sqlite3
db = sqlite3.connect("/var/lib/freeengine/data/tfe.db")
k, email = os.environ["KEY"], os.environ["EMAIL"].lower()
row = db.execute("select id, verified from users where email=?", (email,)).fetchone()
if not row: raise SystemExit(f"no account {email}")
if not row[1]: raise SystemExit("account not confirmed")
with db:
    db.execute("insert into api_keys(hash,user_id,name,prefix,created) values(?,?,?,?,?)",
               (hashlib.sha256(k.encode()).hexdigest(), row[0], os.environ["NAME"], k[:10],
                dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
n = db.execute("select count(*) from api_keys where user_id=? and revoked=0", (row[0],)).fetchone()[0]
print(f"key added for {email} ({n} active keys)")
EOF
