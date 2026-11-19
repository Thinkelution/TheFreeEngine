#!/usr/bin/env bash
# Install ACE-Step 1.5 with CPU torch in ~/acestep-test (skips CUDA, vLLM, gradio and training extras).
set -euo pipefail
D=~/acestep-test
mkdir -p $D && cd $D
[ -d ACE-Step-1.5 ] || git clone -q --depth 1 https://github.com/ace-step/ACE-Step-1.5.git
[ -x venv/bin/python ] || python3 -m venv venv
P=$D/venv/bin/pip
$P install -q --upgrade pip
$P install -q torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
nice -n 10 $P install -q --extra-index-url https://download.pytorch.org/whl/cpu \
  "transformers>=4.51.0,<4.58.0" "diffusers>=0.37.0" scipy soundfile loguru einops "accelerate>=1.12.0" \
  diskcache numba "vector-quantize-pytorch>=1.27.15" toml modelscope huggingface_hub peft matplotlib
$P install -q --no-deps -e ACE-Step-1.5
$D/venv/bin/python -c "import torch, acestep.inference; print('ok torch', torch.__version__)"
du -sh $D
