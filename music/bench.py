"""Time ACE-Step 1.5 turbo on CPU: one instrumental and one short song. No planning LM."""
import os
import sys
import time

import torch

ROOT = os.path.expanduser("~/acestep-test/ACE-Step-1.5")
sys.path.insert(0, ROOT)
from acestep.handler import AceStepHandler  # noqa: E402
from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: E402

torch.set_num_threads(int(os.environ.get("THREADS", os.cpu_count() or 4)))
out = os.path.expanduser("~/acestep-test/out")
os.makedirs(out, exist_ok=True)
t = time.time()
dit = AceStepHandler()
msg, ok = dit.initialize_service(project_root=ROOT, config_path="acestep-v15-turbo", device="cpu")
print(f"init {time.time()-t:.0f}s ok={ok} threads={torch.get_num_threads()} :: {str(msg)[:200]}", flush=True)

jobs = [
    ("instrumental30", dict(caption="calm lofi hip hop beat with soft piano and vinyl crackle", lyrics="[Instrumental]",
                            duration=int(os.environ.get("DUR", "30")), bpm=80)),
    ("song30", dict(caption="upbeat pop song with bright female vocals and acoustic guitar",
                    lyrics="[Verse]\nSunlight on the window, coffee on the stove\n[Chorus]\nEvery little morning feels like coming home",
                    vocal_language="en", duration=int(os.environ.get("DUR", "30")), bpm=110)),
]
for name, p in jobs:
    t = time.time()
    r = generate_music(dit, None, GenerationParams(task_type="text2music", inference_steps=8, shift=3.0, thinking=False,
                                                   use_cot_metas=False, use_cot_caption=False, use_cot_language=False, **p),
                       GenerationConfig(batch_size=1, audio_format="mp3"), save_dir=out)
    dt = time.time() - t
    print(f"{name}: ok={r.success} took {dt:.0f}s for {p['duration']}s audio ({p['duration']/dt:.2f}x realtime) "
          f"{[a.get('path') for a in (r.audios or [])]} {r.error or ''}", flush=True)
