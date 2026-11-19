"""Write web/peaks.json: 64 waveform bars (0-1) per house voice sample, for the voice cards."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

samples = Path(sys.argv[1])
out = {}
for mp3 in sorted(samples.glob("*.mp3")):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, np.int16).astype(np.float32))
    bars = np.array([c.max() if len(c) else 0 for c in np.array_split(x, 64)])
    bars = (bars / max(bars.max(), 1)) ** 0.8
    out[mp3.stem] = [round(float(b), 3) for b in bars]
Path(sys.argv[2]).write_text(json.dumps(out, separators=(",", ":")))
print(f"peaks for {len(out)} voices")
