# TheFreeEngine

Free, open-source speech APIs: text-to-speech, voice cloning and (later) music.

Website and API: https://freeengine.thinkelution.com (served from the 208 server)

## What runs where

| Part | Where | Why |
|---|---|---|
| API server (`server/`) | CPU box (`208.110.72.147`) | Pocket TTS runs on CPU |
| Voice building / training (`gpu/`, `voices/`) | AWS L40S `gpu-worker`, on demand | Scoring and fine-tuning need a GPU |
| Website (`web/`) | Same box, nginx | One domain for site and API |

## Models

| Job | Model | License |
|---|---|---|
| TTS + voice clone | [Kyutai Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (100M, CPU) | MIT code, CC-BY-4.0 weights |
| Music (planned) | [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5) | Apache-2.0 |

Pocket TTS bans cloning a voice without the speaker's consent. The clone API
requires the caller to confirm consent, and every key has a daily clone limit.

## House voices

`voices/build_voices.py` picks the cleanest speakers from
[LibriTTS-R](https://www.openslr.org/141/) (CC BY 4.0, recordings from LibriVox),
scores them with UTMOS (an audio quality score), clones each with Pocket TTS,
scores the cloned speech again, and keeps the best. Output:

- `voices/<id>.safetensors`: voice embedding, loads fast, no gated weights needed
- `voices/catalog.json`: names, sources, scores
- `web/samples/<id>.mp3`: demo clips

See `voices/ATTRIBUTION.md`.

## GPU worker

```powershell
./gpu/gpu.ps1 start          # boot the L40S (about $2.2/hour while on)
./gpu/gpu.ps1 run gpu/setup.sh
./gpu/gpu.ps1 run voices/build_voices.sh
./gpu/gpu.ps1 pull voices    # copy results from S3 into the repo
./gpu/gpu.ps1 stop           # always stop it when done
```

The worker has no open ports. Commands go through AWS SSM; files go through
`s3://gpu-worker-132442190413/thefreeengine/`.

Voice cloning weights are gated on Hugging Face. Accept the terms at
https://huggingface.co/kyutai/pocket-tts, then store a read token once:

```powershell
aws ssm put-parameter --name /thefreeengine/hf_token --type SecureString --value <hf_token>
```

## API server

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python keys.py create my-app      # prints an API key once
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8710
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/voices` | none | voice list |
| POST | `/v1/tts` | `{"text", "voice", "format": "mp3"\|"wav"}` | audio |
| POST | `/v1/voices/clone` | form: `file`, `name`, `consent=yes` | `{"voice": "u_..."}` |
| GET | `/v1/usage` | none | today's use and limits |

Send the key as `Authorization: Bearer <key>`.

## Deploy

```powershell
./deploy/deploy.ps1              # package and install on 208 (systemd: freeengine, nginx, certbot)
./deploy/deploy.ps1 -SetMailKey  # also (re)write the MailerSend key from F:/work/keys.txt
```n
Secrets live in `/etc/freeengine.env` (mode 600). Data lives in `/var/lib/freeengine`. Admin: `TFE_ADMIN_EMAILS`.

## MCP (AI agents)

`POST /mcp` is a Model Context Protocol server (streamable HTTP, stateless, JSON responses).
It uses the same API key as the REST API.

| Tool | Does |
|---|---|
| `list_voices` | Voices with gender, tone, pitch and moods (optional filters) |
| `text_to_speech` | Speech as inline audio plus a download link valid 1 hour |
| `make_music` / `music_status` | Queue an instrumental track, then get its link |
| `voice_status` | Status of one of your cloned voices |
| `get_usage` | Today's use and limits |

Client config:

```json
{ "mcpServers": { "the-free-engine": { "type": "http", "url": "https://thefreeengine.site/mcp",
  "headers": { "Authorization": "Bearer YOUR_TFE_KEY" } } } }
```

Stdio-only clients: `npx -y mcp-remote https://thefreeengine.site/mcp --header "Authorization: Bearer YOUR_TFE_KEY"`

Tests: `server/mcp_test.sh` (curl, 18 checks) and `server/mcp_local_test.ps1` (official MCP Python SDK client).
