#!/usr/bin/env bash
# For each nginx site: server names, backend ports, whether each port is listening.
for f in /etc/nginx/sites-enabled/*; do
  names=$(grep -hoE 'server_name[^;]+' "$f" | head -1 | sed 's/server_name//; s/^ *//')
  ports=$(grep -hoE 'proxy_pass https?://(127\.0\.0\.1|localhost):[0-9]+' "$f" | grep -oE '[0-9]+$' | sort -u | tr '\n' ' ')
  st=""
  for p in $ports; do ss -ltn "sport = :$p" | grep -q LISTEN && st="$st $p:up" || st="$st $p:DOWN"; done
  echo "$(basename "$f") | ${names:-?} |${st:- static}"
done
echo "--- pm2"; for u in root administrator broadcast; do sudo -n -u $u bash -lc 'command -v pm2 >/dev/null && pm2 jlist 2>/dev/null' 2>/dev/null | python3 -c 'import json,sys
try:
  for p in json.load(sys.stdin): print(p["name"], p["pm2_env"]["status"])
except Exception: pass' | sed "s/^/$u: /"; done
echo "--- docker"; sudo -n docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | head -30
echo "--- user services"; systemctl list-units --type=service --all --no-pager --no-legend | grep -vE 'dhcp-interface|systemd-|getty|snap' | awk '$3!="active" || $4!="running"{print $1, $3, $4}' | grep -vE 'inactive dead' | head -20
echo "--- enabled but not running"; for s in $(systemctl list-unit-files --type=service --state=enabled --no-legend | awk '{print $1}'); do systemctl is-active -q $s || echo "$s $(systemctl is-active $s)"; done | grep -vE 'dhcp-interface|cloud-|systemd-|getty|snap|apport|ua-|ubuntu' | head -20
