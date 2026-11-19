#!/usr/bin/env bash
# Start the API on a local port, exercise every endpoint, then stop it.
# Usage: smoke_test.sh <venv-dir> <wav-for-cloning>
set -u
VENV=${1:?venv}; WAV=${2:?wav}
cd "$(dirname "$0")"
export TFE_DATA=${TFE_DATA:-$(mktemp -d)} TFE_BUILTIN_VOICES=${TFE_BUILTIN_VOICES:-alba,marius}
PORT=8710; U=http://127.0.0.1:$PORT
KEY=$("$VENV/bin/python" keys.py create smoke-test 2>/dev/null | tail -1)
"$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port $PORT > "$TFE_DATA/uvicorn.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for _ in $(seq 1 90); do curl -sf $U/v1/health && break; sleep 2; done; echo
A="Authorization: Bearer $KEY"; J='Content-Type: application/json'
curl -s -o /dev/null -w "no key -> %{http_code}\n" $U/v1/voices
curl -s -H "$A" $U/v1/voices; echo
curl -s -o "$TFE_DATA/api.mp3" -w "tts mp3 -> %{http_code} %{size_download}B %{time_total}s\n" -H "$A" -H "$J" \
  -d '{"text":"Hello from The Free Engine API.","voice":"marius"}' $U/v1/tts
P=()
for i in 1 2; do
  curl -s -o /dev/null -w "parallel $i -> %{http_code} %{time_total}s\n" -H "$A" -H "$J" \
    -d '{"text":"Two requests at the same time should both work fine.","voice":"alba","format":"wav"}' $U/v1/tts &
  P+=($!)
done; wait "${P[@]}"
curl -s -o /dev/null -w "bad voice -> %{http_code}\n" -H "$A" -H "$J" -d '{"text":"hi","voice":"nope"}' $U/v1/tts
echo fake > "$TFE_DATA/x.txt"
curl -s -H "$A" -F file=@"$TFE_DATA/x.txt" -F name=t -F consent=yes $U/v1/voices/clone; echo
curl -s -H "$A" -F file=@"$WAV" -F name=t $U/v1/voices/clone; echo
curl -s -H "$A" -F file=@"$WAV" -F name=mine -F consent=yes $U/v1/voices/clone; echo
curl -s -H "$A" $U/v1/usage; echo
grep -iE "traceback|error" "$TFE_DATA/uvicorn.log" | head -5
echo "data: $TFE_DATA"
