#!/usr/bin/env bash
# Check the Hugging Face token and access to the gated Pocket TTS cloning weights.
export HOME=/root
/opt/tfe-venv/bin/python - <<'EOF'
from huggingface_hub import HfApi, list_repo_files, hf_hub_download
api = HfApi()
try:
    me = api.whoami()
    print("token user:", me.get("name"), "| token role:", me.get("auth", {}).get("accessToken", {}).get("role"))
except Exception as e:
    print("whoami failed:", type(e).__name__, str(e)[:200])
try:
    files = list_repo_files("kyutai/pocket-tts")
    print("repo files:", [f for f in files if f.endswith((".safetensors", ".yaml"))][:12])
    for f in files:
        if f.endswith(".safetensors"):
            hf_hub_download("kyutai/pocket-tts", f)
            print("downloaded OK:", f)
except Exception as e:
    print("gated access failed:", type(e).__name__, str(e)[:300])
EOF
