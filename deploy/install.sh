#!/usr/bin/env bash
# Install or update The Free Engine on the 208 server. Run as root:
#   sudo bash install.sh <release.tar.gz>        (MailerSend key on stdin the first time, or to replace it)
set -euo pipefail
TAR=${1:?release tarball}
DOMAIN=freeengine.thinkelution.com
PORT=18710
APP=/opt/freeengine
VAR=/var/lib/freeengine
ENVF=/etc/freeengine.env
TS=$(date +%Y%m%d-%H%M%S)

id freeengine >/dev/null 2>&1 || useradd --system --home-dir $VAR --shell /usr/sbin/nologin freeengine
install -d -o freeengine -g freeengine -m 750 $VAR $VAR/data $VAR/hf
install -d -m 755 $APP $APP/releases
REL=$APP/releases/$TS
mkdir -p "$REL" && tar -xzf "$TAR" -C "$REL"

# Python env (CPU-only torch). Reinstalled only when requirements change.
REQ_SUM=$(sha256sum "$REL/server/requirements.txt" | cut -c1-16)
if [ ! -x $APP/venv/bin/uvicorn ] || [ "$(cat $APP/venv/.req 2>/dev/null)" != "$REQ_SUM" ]; then
  python3 -m venv $APP/venv
  $APP/venv/bin/pip install -q --upgrade pip
  $APP/venv/bin/pip install -q -r "$REL/server/requirements.txt" --extra-index-url https://download.pytorch.org/whl/cpu
  echo "$REQ_SUM" > $APP/venv/.req
fi

# Hugging Face token for the gated cloning weights (copied once from the admin's login).
if [ ! -s $VAR/hf/token ] && [ -s /home/administrator/.cache/huggingface/token ]; then
  install -o freeengine -g freeengine -m 600 /home/administrator/.cache/huggingface/token $VAR/hf/token
fi

# Secrets file. MailerSend key comes on stdin (never on the command line).
if [ ! -t 0 ]; then MS_KEY=$(tr -d '\r\n'); else MS_KEY=""; fi
if [ ! -f $ENVF ] || [ -n "$MS_KEY" ]; then
  OLD_KEY=$(grep -s '^MAILERSEND_API_KEY=' $ENVF | cut -d= -f2- || true)
  install -m 600 /dev/null $ENVF
  cat > $ENVF <<EOF
TFE_BASE_URL=https://$DOMAIN
TFE_ADMIN_EMAILS=vivek@thinkelution.com
TFE_MAIL_FROM=vivek@thinkelution.com
TFE_MAIL_FROM_NAME=The Free Engine
TFE_DATA=$VAR/data
TFE_VOICES=$APP/current/voices
TFE_WORKERS=2
HF_HOME=$VAR/hf
MAILERSEND_API_KEY=${MS_KEY:-$OLD_KEY}
EOF
  chmod 600 $ENVF
fi

cat > /etc/systemd/system/freeengine.service <<EOF
[Unit]
Description=The Free Engine speech API
After=network.target
[Service]
User=freeengine
Group=freeengine
WorkingDirectory=$APP/current/server
EnvironmentFile=$ENVF
ExecStart=$APP/venv/bin/uvicorn app:app --host 127.0.0.1 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=5
Nice=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$VAR
[Install]
WantedBy=multi-user.target
EOF

# Waveform bars for the voice cards, then make the release world-readable.
$APP/venv/bin/python "$REL/deploy/make_peaks.py" "$REL/web/samples" "$REL/web/peaks.json"
chown -R root:root "$REL" && chmod -R a+rX,go-w "$REL"

ln -sfn "$REL" $APP/current.new && mv -Tf $APP/current.new $APP/current

# nginx: HTTP first; certbot adds TLS on the first run.
SITE=/etc/nginx/sites-available/$DOMAIN
if [ ! -f $SITE ] || ! grep -q "managed by Certbot" $SITE; then
  sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__PORT__/$PORT/g" -e "s#__ROOT__#$APP/current/web#g" "$REL/deploy/nginx.conf" > $SITE
  ln -sfn $SITE /etc/nginx/sites-enabled/$DOMAIN
  nginx -t && systemctl reload nginx
  certbot --nginx -d $DOMAIN --non-interactive --agree-tos --redirect -m vivek@thinkelution.com || echo "CERTBOT FAILED"
  nginx -t && systemctl reload nginx
fi

systemctl daemon-reload
systemctl enable -q freeengine
systemctl restart freeengine
for i in $(seq 1 90); do curl -sf http://127.0.0.1:$PORT/v1/health && break; sleep 2; done; echo
systemctl is-active freeengine

# Keep the last 3 releases.
ls -1dt $APP/releases/* | tail -n +4 | xargs -r rm -rf
echo "deployed $TS"
