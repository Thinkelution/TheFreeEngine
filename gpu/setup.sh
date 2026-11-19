#!/usr/bin/env bash
# One-time (idempotent) setup of the GPU worker. Runs as root through SSM.
set -euo pipefail
export HOME=/root
export PATH="$HOME/.local/bin:$PATH"

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v ffmpeg >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg)

# Hugging Face token for the gated voice-cloning weights (stored by the owner in SSM).
TOKEN=$(aws ssm get-parameter --region us-east-1 --name /thefreeengine/hf_token \
        --with-decryption --query Parameter.Value --output text 2>/dev/null || true)
if [ -n "$TOKEN" ] && [ "$TOKEN" != "None" ]; then
  mkdir -p "$HOME/.cache/huggingface"
  umask 077; printf '%s' "$TOKEN" > "$HOME/.cache/huggingface/token"
  echo "HF token installed."
else
  echo "WARNING: no /thefreeengine/hf_token in SSM; voice cloning will not work."
fi

[ -d /opt/tfe-venv ] || uv venv /opt/tfe-venv --python 3.12
uv pip install --python /opt/tfe-venv -q \
  pocket-tts datasets soundfile librosa scipy numpy huggingface_hub
/opt/tfe-venv/bin/python - <<'EOF'
import torch, pocket_tts
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
EOF
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
df -h /opt | tail -1
