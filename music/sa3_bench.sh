#!/usr/bin/env bash
# Install Stable Audio 3 (CPU torch) in ~/sa3-test.  Usage: sa3_bench.sh install | run
set -euo pipefail
D=~/sa3-test
mkdir -p $D && cd $D
if [ "${1:-run}" = install ]; then
  [ -d stable-audio-3 ] || git clone -q --depth 1 https://github.com/Stability-AI/stable-audio-3.git
  [ -x venv/bin/python ] || python3 -m venv venv
  venv/bin/pip install -q --upgrade pip
  venv/bin/pip install -q torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
  sed -i -E 's/"(torch|torchaudio|torchvision)==[0-9.]+"/"\1"/g' stable-audio-3/pyproject.toml
  nice -n 10 venv/bin/pip install -q -e stable-audio-3 --extra-index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -3
  venv/bin/pip install -q soundfile
  venv/bin/python -c "import torch, stable_audio_3; print('ok torch', torch.__version__)"
  du -sh $D
  exit 0
fi
cat > bench.py <<'EOF'
import os, time, torch, soundfile as sf, numpy as np
torch.set_num_threads(int(os.environ.get("THREADS", "8")))
from stable_audio_3 import StableAudioModel
t = time.time()
m = StableAudioModel.from_pretrained("small-music", device="cpu", model_half=False)  # fp16 crashes on pre-AVX2 CPUs
print(f"load {time.time()-t:.0f}s threads={torch.get_num_threads()}", flush=True)
for name, prompt, dur in [
    ("lofi30", "Calm lofi hip hop beat with soft piano, warm vinyl crackle and mellow drums 80 BPM", 30),
    ("edm30", "Upbeat house track with punchy kick, bright synth chords and a big build 124 BPM", 30),
    ("ambient60", "Slow cinematic ambient pad with gentle strings and distant piano", 60),
]:
    t = time.time()
    a = m.generate(prompt=prompt, duration=dur)
    dt = time.time() - t
    x = a.detach().float().cpu().numpy() if hasattr(a, "detach") else np.asarray(a)
    x = np.squeeze(x)
    if x.ndim == 2 and x.shape[0] < x.shape[1]:
        x = x.T
    sr = getattr(m, "sample_rate", 44100)
    sf.write(f"{name}.wav", x / max(np.abs(x).max(), 1e-6) * 0.95, sr)
    print(f"{name}: {dur}s audio in {dt:.0f}s ({dur/dt:.2f}x realtime) shape={x.shape} sr={sr}", flush=True)
EOF
venv/bin/python bench.py
