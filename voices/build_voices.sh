#!/usr/bin/env bash
# Build the house voices on the GPU worker, then upload results to S3.
set -euo pipefail
export HOME=/root
OUT=/opt/tfe/out/voices
rm -rf "$OUT" && mkdir -p "$OUT"
/opt/tfe-venv/bin/python /opt/tfe/voices/build_voices.py "$OUT"
aws s3 sync "$OUT" s3://gpu-worker-132442190413/thefreeengine/out/voices --only-show-errors --delete
echo "Uploaded $(ls "$OUT" | wc -l) files."
# Temp build boxes are launched with shutdown-behavior=terminate: power off when done.
if [ -f /opt/tfe-self-terminate ] || [ "${TFE_SELF_TERMINATE:-0}" = 1 ]; then
  (sleep 60; shutdown -h now) >/dev/null 2>&1 &
fi
