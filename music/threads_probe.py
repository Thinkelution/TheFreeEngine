"""Make one short clip with THREADS threads and print the speed."""
import os, time, torch
torch.set_num_threads(int(os.environ["THREADS"]))
from stable_audio_3 import StableAudioModel
m = StableAudioModel.from_pretrained("small-music", device="cpu", model_half=False)
t = time.time()
m.generate(prompt="Calm lofi hip hop beat with soft piano 80 BPM", duration=10)
print(f"threads={os.environ['THREADS']} 10s audio in {time.time()-t:.0f}s", flush=True)
