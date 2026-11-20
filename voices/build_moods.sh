#!/usr/bin/env bash
# Build mood versions of the house voices on the GPU worker, upload to S3, then power off (temp box).
set -euo pipefail
export HOME=/root PATH=/root/.local/bin:$PATH
B=s3://gpu-worker-132442190413/thefreeengine
uv pip install --python /opt/tfe-venv -q funasr modelscope 2>&1 | tail -2
SRC=/opt/tfe/voices-src; OUT=/opt/tfe/out/moods
rm -rf "$OUT" "$SRC"; mkdir -p "$OUT" "$SRC"
aws s3 sync $B/out/voices "$SRC" --only-show-errors --exclude '*.mp3' --exclude 'refs/*'
ls "$SRC" | wc -l
/opt/tfe-venv/bin/python /opt/tfe/voices/build_moods.py "$SRC" "$OUT"
[ -n "${TFE_LIMIT_VOICES:-}" ] && { echo "test run, not uploading"; exit 0; }
aws s3 sync "$OUT" $B/out/moods --only-show-errors --delete
echo "Uploaded $(ls "$OUT" | wc -l) files."
if [ -f /opt/tfe-self-terminate ]; then (sleep 60; shutdown -h now) >/dev/null 2>&1 & fi
