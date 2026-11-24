#!/usr/bin/env bash
# Install or update The Free Engine on the 208 server. Run as root:
#   sudo bash install.sh <release.tar.gz>        (MailerSend key on stdin the first time, or to replace it)
set -euo pipefail
TAR=${1:?release tarball}
DOMAIN=thefreeengine.site   # main domain (links in emails); freeengine.thinkelution.com also works
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

# Music env: Stable Audio 3 with CPU torch, kept apart from the TTS env. Only with TFE_MUSIC=1 (needs ~6 GB RAM).
if [ "${TFE_MUSIC:-0}" = 1 ] && { [ ! -x $APP/music-venv/bin/python ] || [ ! -f $APP/music-venv/.ok ]; }; then
  [ -d $APP/stable-audio-3 ] || git clone -q --depth 1 https://github.com/Stability-AI/stable-audio-3.git $APP/stable-audio-3
  sed -i -E 's/"(torch|torchaudio|torchvision)==[0-9.]+"/"\1"/g' $APP/stable-audio-3/pyproject.toml
  python3 -m venv $APP/music-venv
  $APP/music-venv/bin/pip install -q --upgrade pip
  $APP/music-venv/bin/pip install -q torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
  $APP/music-venv/bin/pip install -q -e $APP/stable-audio-3 soundfile --extra-index-url https://download.pytorch.org/whl/cpu
  touch $APP/music-venv/.ok
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
sed -i "s#^TFE_BASE_URL=.*#TFE_BASE_URL=https://$DOMAIN#" $ENVF

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
# Crawlable voice list, sitemap.xml, robots.txt.
$APP/venv/bin/python "$REL/deploy/make_seo.py" "$REL/voices/catalog.json" "$REL/web"
# Version the CSS/JS links so browsers and Cloudflare never serve an old copy after a deploy.
for f in app.js app.css; do
  v=$(sha256sum "$REL/web/$f" | cut -c1-10)
  sed -i "s#/$f\"#/$f?v=$v\"#" "$REL/web/index.html"
done
chown -R root:root "$REL" && chmod -R a+rX,go-w "$REL"

ln -sfn "$REL" $APP/current.new && mv -Tf $APP/current.new $APP/current

# nginx: one site file per domain group; HTTP first, certbot adds TLS on the first run.
for GROUP in "thefreeengine.site www.thefreeengine.site" "freeengine.thinkelution.com"; do
  MAIN=${GROUP%% *}
  SITE=/etc/nginx/sites-available/$MAIN
  if [ ! -f $SITE ] || ! grep -q "managed by Certbot" $SITE; then
    sed -e "s/__DOMAIN__/$GROUP/g" -e "s/__PORT__/$PORT/g" -e "s#__ROOT__#$APP/current/web#g" "$REL/deploy/nginx.conf" > $SITE
    ln -sfn $SITE /etc/nginx/sites-enabled/$MAIN
    nginx -t && systemctl reload nginx
    certbot --nginx $(printf -- '-d %s ' $GROUP) --non-interactive --agree-tos --redirect -m vivek@thinkelution.com || echo "CERTBOT FAILED for $GROUP"
    nginx -t && systemctl reload nginx
  fi
done
# Route /mcp to the app on the existing (certbot-managed) site files.
for MAIN in thefreeengine.site freeengine.thinkelution.com; do
  S=/etc/nginx/sites-available/$MAIN
  if [ -f $S ] && grep -q 'location ~ \^/(v1|api|auth)/ {' $S; then
    cp $S /tmp/$MAIN.bak
    sed -i 's#location ~ ^/(v1|api|auth)/ {#location ~ ^/(v1|api|auth|mcp)(/|$) {#' $S
    nginx -t && systemctl reload nginx || { cp /tmp/$MAIN.bak $S; systemctl reload nginx; echo "mcp route FAILED for $MAIN, reverted"; }
  fi
done
# One address for search engines: www -> apex (301), on the HTTPS server block.
S=/etc/nginx/sites-available/thefreeengine.site
if [ -f $S ] && ! grep -q 'www.thefreeengine.site) { return 301' $S; then
  cp $S /tmp/thefreeengine.site.bak
  awk '/listen 443 ssl/ && !done { print "    if ($host = www.thefreeengine.site) { return 301 https://thefreeengine.site$request_uri; }"; done=1 } { print }' $S > $S.new && mv $S.new $S
  nginx -t && systemctl reload nginx || { cp /tmp/thefreeengine.site.bak $S; systemctl reload nginx; echo "www redirect FAILED, reverted"; }
fi

cat > /etc/systemd/system/freeengine-music.service <<EOF
[Unit]
Description=The Free Engine music worker (Stable Audio 3 Small, CPU)
After=network.target freeengine.service
[Service]
User=freeengine
Group=freeengine
WorkingDirectory=$APP/current/music
EnvironmentFile=$ENVF
Environment=TFE_MUSIC_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
ExecStart=$APP/music-venv/bin/python $APP/current/music/worker.py
Restart=on-failure
RestartSec=30
Nice=15
CPUQuota=150%
MemoryMax=6G
MemorySwapMax=0
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$VAR
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable -q freeengine
systemctl restart freeengine
if [ "${TFE_MUSIC:-0}" = 1 ]; then
  sed -i '/^TFE_MUSIC_ENABLED=/d' $ENVF; echo "TFE_MUSIC_ENABLED=1" >> $ENVF
  systemctl enable -q freeengine-music; systemctl restart freeengine-music; systemctl restart freeengine
else
  sed -i '/^TFE_MUSIC_ENABLED=/d' $ENVF
  systemctl disable --now -q freeengine-music 2>/dev/null || true
  echo "music worker: $(systemctl is-active freeengine-music 2>/dev/null || true) / $(systemctl is-enabled freeengine-music 2>/dev/null || true)"
fi
for i in $(seq 1 90); do curl -sf http://127.0.0.1:$PORT/v1/health && break; sleep 2; done; echo
systemctl is-active freeengine

# Keep the last 3 releases.
ls -1dt $APP/releases/* | tail -n +4 | xargs -r rm -rf
echo "deployed $TS"
