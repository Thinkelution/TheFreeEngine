#!/usr/bin/env bash
# Test the /mcp endpoint on a private port (not prod). Usage: mcp_test.sh <venv> <voices-dir>
set -u
VENV=${1:?venv}; VOICES=${2:?voices}
cd "$(dirname "$0")"
D=$(mktemp -d); trap 'kill $PID 2>/dev/null; rm -rf "$D"' EXIT
export TFE_DATA=$D TFE_VOICES=$VOICES TFE_MAIL_LOG=$D/mail.log TFE_COOKIE_SECURE=0 TFE_BASE_URL=http://127.0.0.1:8712
U=http://127.0.0.1:8712; H='X-TFE: 1'; J='Content-Type: application/json'
"$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port 8712 > "$D/app.log" 2>&1 &
PID=$!
for _ in $(seq 1 120); do curl -sf $U/v1/health >/dev/null && break; sleep 2; done
ok() { [ "$2" = "$3" ] && echo "PASS $1" || echo "FAIL $1: got [$2] want [$3]"; }
curl -s -o /dev/null -X POST -H "$H" -H "$J" -d '{"email":"m@example.com","password":"password123"}' $U/auth/signup; sleep 1
T=$(grep -o 'token=[A-Za-z0-9_-]*' $D/mail.log | head -1 | cut -d= -f2)
curl -s -o /dev/null -c $D/c -X POST -H "$H" -H "$J" -d "{\"token\":\"$T\"}" $U/auth/verify
KEY=$(curl -s -b $D/c -X POST -H "$H" -H "$J" -d '{"name":"mcp"}' $U/api/keys | grep -o 'tfe_[A-Za-z0-9_-]*')
A="Authorization: Bearer $KEY"; M=$U/mcp
rpc() { curl -s -X POST -H "$J" -H "Accept: application/json, text/event-stream" "$@" $M; }

ok initialize "$(rpc -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' | grep -o '"protocolVersion":"2025-06-18"')" '"protocolVersion":"2025-06-18"'
ok notification-202 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$J" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' $M)" 202
ok no-key-401 "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$J" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' $M)" 401
ok tools-list "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | grep -o '"name":"[a-z_]*"' | wc -l | tr -d ' ')" 6
ok list-voices "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_voices","arguments":{"gender":"f","mood":"sad"}}}' | grep -o 'count[^,]*' | head -1 | grep -c '[0-9]')" 1
R=$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"text_to_speech","arguments":{"text":"Hello from an AI agent.","voice":"june","mood":"excited"}}}')
ok tts-audio "$(echo "$R" | grep -o '"type":"audio"')" '"type":"audio"'
ok tts-mood "$(echo "$R" | grep -o '\\"mood\\": \\"[a-z]*\\"' | head -1)" '\"mood\": \"excited\"'
LINK=$(echo "$R" | grep -o 'http://127.0.0.1:8712/v1/files/[A-Za-z0-9_.-]*' | head -1)
ok tts-link "$(curl -s -o $D/a.mp3 -w '%{http_code}' "$LINK")" 200
ok tts-link-mp3 "$(file -b $D/a.mp3 | grep -ciE 'mpeg|id3' | head -1)" 1
ok link-bad-name "$(curl -s -o /dev/null -w '%{http_code}' $U/v1/files/..%2Fetc)" 404
ok tts-no-audio "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"text_to_speech","arguments":{"text":"Link only.","include_audio":false}}}' | grep -c '"type":"audio"')" 0
ok tts-too-long "$(rpc -H "$A" -d "{\"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",\"params\":{\"name\":\"text_to_speech\",\"arguments\":{\"text\":\"$(head -c 1100 /dev/zero | tr '\0' a)\"}}}" | grep -o '"isError":true')" '"isError":true'
ok bad-mood "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"text_to_speech","arguments":{"text":"x","mood":"angry"}}}' | grep -o '"isError":true')" '"isError":true'
MR=$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"make_music","arguments":{"prompt":"calm lofi beat","duration":10}}}')
MID=$(echo "$MR" | grep -o 'm_[0-9a-f]*' | head -1)
ok make-music "$([ -n "$MID" ] && echo yes)" yes
ok music-status "$(rpc -H "$A" -d "{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"tools/call\",\"params\":{\"name\":\"music_status\",\"arguments\":{\"id\":\"$MID\"}}}" | grep -o 'queued' | head -1)" queued
ok usage "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"get_usage","arguments":{}}}' | grep -o 'chars_limit' | head -1)" chars_limit
ok unknown-tool "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"nope"}}' | grep -o '"code":-32602')" '"code":-32602'
ok unknown-method "$(rpc -H "$A" -d '{"jsonrpc":"2.0","id":12,"method":"nope"}' | grep -o '"code":-32601')" '"code":-32601'
echo "--- app errors:"; grep -E "Traceback|ERROR" "$D/app.log" | head -5
