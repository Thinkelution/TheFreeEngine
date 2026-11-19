#!/usr/bin/env bash
# End-to-end test of the API on a private port: signup -> verify -> key -> tts -> clone queue -> admin.
# Usage: smoke_test.sh <venv-dir> <voices-dir> <wav-for-cloning>
set -u
VENV=${1:?venv}; VOICES=${2:?voices}; WAV=${3:?wav}
cd "$(dirname "$0")"
D=$(mktemp -d); trap 'kill $PID 2>/dev/null; rm -rf "$D"' EXIT
export TFE_DATA=$D TFE_VOICES=$VOICES TFE_MAIL_LOG=$D/mail.log TFE_COOKIE_SECURE=0 \
       TFE_ADMIN_EMAILS=admin@example.com TFE_BASE_URL=http://127.0.0.1:8711
U=http://127.0.0.1:8711; H='X-TFE: 1'; J='Content-Type: application/json'
"$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port 8711 > "$D/app.log" 2>&1 &
PID=$!
for _ in $(seq 1 120); do curl -sf $U/v1/health >/dev/null && break; sleep 2; done
c() { curl -s -o /dev/null -w "%{http_code}" "$@"; }
tok() { grep "\"to\": \"$1\"" "$D/mail.log" | tail -1 | grep -o 'token=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2; }
ok() { [ "$2" = "$3" ] && echo "PASS $1 ($2)" || echo "FAIL $1: got $2 want $3"; }

ok health "$(curl -s $U/v1/health | grep -o '"voices":[0-9]*')" '"voices":60'
ok public-voices "$(curl -s $U/api/voices | grep -o '"type":"house"' | wc -l)" 60
ok signup-no-csrf "$(c -X POST -H "$J" -d '{"email":"a@example.com","password":"password123"}' $U/auth/signup)" 403
ok signup-short-pw "$(c -X POST -H "$H" -H "$J" -d '{"email":"a@example.com","password":"short"}' $U/auth/signup)" 400
ok signup "$(c -X POST -H "$H" -H "$J" -d '{"email":"A@Example.com","password":"password123"}' $U/auth/signup)" 200
sleep 1
ok login-unverified "$(c -X POST -H "$H" -H "$J" -d '{"email":"a@example.com","password":"password123"}' $U/auth/login)" 403
T=$(tok a@example.com); ok got-verify-token "$([ -n "$T" ] && echo yes)" yes
ok verify "$(c -c $D/ca -X POST -H "$H" -H "$J" -d "{\"token\":\"$T\"}" $U/auth/verify)" 200
ok verify-reuse "$(c -X POST -H "$H" -H "$J" -d "{\"token\":\"$T\"}" $U/auth/verify)" 400
ok me "$(curl -s -b $D/ca $U/api/me | grep -o '"admin":false')" '"admin":false'
ok bad-pw "$(c -X POST -H "$H" -H "$J" -d '{"email":"a@example.com","password":"wrongwrong"}' $U/auth/login)" 401
ok login "$(c -c $D/ca -X POST -H "$H" -H "$J" -d '{"email":"a@example.com","password":"password123"}' $U/auth/login)" 200
KEY=$(curl -s -b $D/ca -X POST -H "$H" -H "$J" -d '{"name":"t"}' $U/api/keys | grep -o 'tfe_[A-Za-z0-9_-]*')
ok key "$([ ${#KEY} -gt 30 ] && echo yes)" yes
ok tts-nokey "$(c -X POST -H "$J" -d '{"text":"hi"}' $U/v1/tts)" 401
ok tts-key "$(curl -s -o $D/a.mp3 -w '%{http_code}' -X POST -H "Authorization: Bearer $KEY" -H "$J" -d '{"text":"Hello from the smoke test.","voice":"leo"}' $U/v1/tts)" 200
ok tts-is-mp3 "$(file -b $D/a.mp3 | grep -oiE 'MPEG|ID3' | head -1 | tr a-z A-Z)" "$(file -b $D/a.mp3 | grep -qi id3 && echo ID3 || echo MPEG)"
ok tts-session "$(c -b $D/ca -X POST -H "$H" -H "$J" -d '{"text":"Session works.","voice":"aria","format":"wav"}' $U/v1/tts)" 200
ok tts-session-nocsrf "$(c -b $D/ca -X POST -H "$J" -d '{"text":"x","voice":"aria"}' $U/v1/tts)" 403
ok tts-bad-voice "$(c -X POST -H "Authorization: Bearer $KEY" -H "$J" -d '{"text":"hi","voice":"nope"}' $U/v1/tts)" 404
ok clone-no-consent "$(c -b $D/ca -H "$H" -F file=@"$WAV" -F name=Mine $U/api/me/voices)" 400
R=$(curl -s -b $D/ca -H "$H" -F file=@"$WAV" -F name=Mine -F visibility=private -F consent=yes $U/api/me/voices); echo "  $R"
VID=$(echo "$R" | grep -o 'u_[0-9a-f]*' | head -1)
for _ in $(seq 1 90); do S=$(curl -s -H "Authorization: Bearer $KEY" $U/v1/voices/$VID | grep -o '"status":"[a-z]*"'); [ "$S" = '"status":"ready"' -o "$S" = '"status":"failed"' ] && break; sleep 3; done
ok clone-ready "$S" '"status":"ready"'
ok clone-email "$(grep -c 'is ready' $D/mail.log)" 1
ok tts-own-clone "$(c -X POST -H "Authorization: Bearer $KEY" -H "$J" -d "{\"text\":\"My own voice.\",\"voice\":\"$VID\"}" $U/v1/tts)" 200
ok second-clone-blocked "$(c -b $D/ca -H "$H" -F file=@"$WAV" -F name=Two -F consent=yes $U/api/me/voices)" 429
ok clones-used "$(curl -s -b $D/ca $U/api/me | grep -o '"clones_used":1')" '"clones_used":1'
MR=$(curl -s -X POST -H "Authorization: Bearer $KEY" -H "$J" -d '{"prompt":"calm lofi beat","duration":10}' $U/v1/music)
MID=$(echo "$MR" | grep -o 'm_[0-9a-f]*' | head -1)
ok music-queued "$(echo "$MR" | grep -o '"status":"queued"')" '"status":"queued"'
ok music-too-long "$(c -X POST -H "Authorization: Bearer $KEY" -H "$J" -d '{"prompt":"calm lofi beat","duration":60}' $U/v1/music)" 400
ok music-status "$(c -H "Authorization: Bearer $KEY" $U/v1/music/$MID)" 200
ok music-audio-not-ready "$(c -H "Authorization: Bearer $KEY" $U/v1/music/$MID/audio)" 404
ok music-list "$(curl -s -b $D/ca $U/api/me/music | grep -c $MID)" 1
ok music-usage "$(curl -s -H "Authorization: Bearer $KEY" $U/v1/usage | grep -o '"music":1')" '"music":1'
ok private-hidden-from-library "$(curl -s $U/api/voices | grep -c $VID)" 0
ok private-sample-anon "$(c $U/api/voices/$VID/sample)" 404
# second user cannot use the private voice
curl -s -o /dev/null -X POST -H "$H" -H "$J" -d '{"email":"b@example.com","password":"password123"}' $U/auth/signup; sleep 1
curl -s -o /dev/null -c $D/cb -X POST -H "$H" -H "$J" -d "{\"token\":\"$(tok b@example.com)\"}" $U/auth/verify
ok other-user-private "$(c -b $D/cb -X POST -H "$H" -H "$J" -d "{\"text\":\"x\",\"voice\":\"$VID\"}" $U/v1/tts)" 404
ok make-public "$(c -b $D/ca -X PATCH -H "$H" -H "$J" -d '{"visibility":"public"}' $U/api/me/voices/$VID)" 200
ok public-in-library "$(curl -s $U/api/voices | grep -c $VID)" 1
ok other-user-public "$(c -b $D/cb -X POST -H "$H" -H "$J" -d "{\"text\":\"x\",\"voice\":\"$VID\"}" $U/v1/tts)" 200
ok non-admin-blocked "$(c -b $D/ca $U/api/admin/stats)" 404
curl -s -o /dev/null -X POST -H "$H" -H "$J" -d '{"email":"admin@example.com","password":"password123"}' $U/auth/signup; sleep 1
curl -s -o /dev/null -c $D/cz -X POST -H "$H" -H "$J" -d "{\"token\":\"$(tok admin@example.com)\"}" $U/auth/verify
ok admin-stats "$(c -b $D/cz $U/api/admin/stats)" 200
ok admin-hide "$(c -b $D/cz -X PATCH -H "$H" -H "$J" -d '{"hidden":true}' $U/api/admin/voices/$VID)" 200
ok hidden-gone "$(curl -s $U/api/voices | grep -c $VID)" 0
BID=$(curl -s -b $D/cz $U/api/admin/users | grep -o '"id":[0-9]*,"email":"b@example.com"' | grep -o '[0-9]*' | head -1)
ok admin-disable "$(c -b $D/cz -X PATCH -H "$H" -H "$J" -d '{"disabled":true}' $U/api/admin/users/$BID)" 200
ok disabled-session "$(c -b $D/cb $U/api/me)" 401
curl -s -o /dev/null -X POST -H "$H" -H "$J" -d '{"email":"a@example.com"}' $U/auth/forgot; sleep 1
RT=$(grep '"to": "a@example.com"' $D/mail.log | grep reset | grep -o 'token=[A-Za-z0-9_-]*' | tail -1 | cut -d= -f2)
ok reset "$(c -X POST -H "$H" -H "$J" -d "{\"token\":\"$RT\",\"password\":\"newpassword1\"}" $U/auth/reset)" 200
ok login-new-pw "$(c -X POST -H "$H" -H "$J" -d '{"email":"a@example.com","password":"newpassword1"}' $U/auth/login)" 200
ok delete-voice "$(c -b $D/ca -X DELETE -H "$H" $U/api/me/voices/$VID)" 401  # old session ended by reset
echo "--- app errors:"; grep -E "Traceback|ERROR" "$D/app.log" | head -5
