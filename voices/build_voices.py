"""Pick the best house voices from LibriTTS-R and export them for Pocket TTS.

1. Stream LibriTTS-R (CC BY 4.0) and keep a few 6-14 s clips per speaker.
2. Score every clip with UTMOS (predicted listener rating, 1-5) on the GPU.
3. Take the cleanest clip of the top speakers, clone each with Pocket TTS,
   and score the *cloned* speech. Reject clones that talk too fast or too slow.
4. Keep the best voices from each pitch group and export:
   <id>.safetensors (voice embedding), <id>.mp3 (demo), refs/<id>.wav, catalog.json
"""
import io
import json
import os
import sys
import tempfile
import time

import librosa
import numpy as np
import soundfile as sf
import torch
from datasets import Audio, load_dataset
from pocket_tts import TTSModel, export_model_state

OUT = sys.argv[1] if len(sys.argv) > 1 else "out/voices"
SPLITS = ["test.clean", "dev.clean", "train.clean.100"]
CLIPS_PER_SPEAKER = 5
MIN_SEC, MAX_SEC = 6.0, 14.0
CANDIDATES_PER_GROUP = int(os.environ.get("TFE_CANDIDATES_PER_GROUP", "34"))
FINAL_PER_GROUP = int(os.environ.get("TFE_FINAL_PER_GROUP", "30"))
HIGH_PITCH_HZ = 165.0
TEST_LINES = [
    "Welcome to The Free Engine. This voice is free for everyone to use.",
    "The old lighthouse keeper climbed the stairs, counting each step out loud, "
    "until the light flickered on over the dark and quiet sea.",
]
NAMES = {
    "high": ["aria", "bella", "clara", "daisy", "ella", "fiona", "grace", "hazel", "iris", "june",
             "kira", "luna", "maya", "nora", "olive", "piper", "quinn", "rose", "sage", "tara",
             "uma", "vera", "willow", "xena", "yara", "zoe", "amber", "bree", "cora", "dawn"],
    "low": ["adam", "ben", "carl", "dan", "eric", "felix", "gavin", "henry", "ivan", "jack",
            "kyle", "leo", "max", "nate", "owen", "paul", "quentin", "reed", "sam", "theo",
            "upton", "vince", "wade", "xavier", "york", "zane", "alan", "brett", "cole", "drew"],
}

dev = "cuda" if torch.cuda.is_available() else "cpu"
print(f"device={dev}", flush=True)
mos_model = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).to(dev).eval()


def utmos(wav: np.ndarray, sr: int) -> float:
    x = torch.as_tensor(wav, dtype=torch.float32, device=dev)[None]
    with torch.no_grad():
        return float(mos_model(x, sr).item())


def median_pitch(wav: np.ndarray, sr: int) -> float:
    f0 = librosa.yin(wav, fmin=60, fmax=400, sr=sr)
    rms = librosa.feature.rms(y=wav)[0][: len(f0)]
    voiced = f0[: len(rms)][rms > np.percentile(rms, 60)]
    return float(np.median(voiced)) if len(voiced) else 0.0


# 1. collect clips
t0 = time.time()
clips: dict[str, list] = {}
for split in SPLITS:
    ds = load_dataset("mythicinfinity/libritts_r", "clean", split=split, streaming=True)
    ds = ds.cast_column("audio", Audio(decode=False))
    n = 0
    for row in ds:
        spk = str(row["speaker_id"])
        if len(clips.get(spk, [])) >= CLIPS_PER_SPEAKER:
            continue
        wav, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        dur = len(wav) / sr
        if not (MIN_SEC <= dur <= MAX_SEC):
            continue
        clips.setdefault(spk, []).append(
            {"id": row["id"], "text": row["text_normalized"], "wav": wav, "sr": sr}
        )
        n += 1
    print(f"{split}: kept {n} clips, {len(clips)} speakers so far ({time.time()-t0:.0f}s)", flush=True)

# 2. score source clips, keep the best clip per speaker
best = []
for spk, cs in clips.items():
    for c in cs:
        c["mos"] = utmos(c["wav"], c["sr"])
    top = max(cs, key=lambda c: c["mos"])
    top["speaker"] = spk
    best.append(top)
best.sort(key=lambda c: -c["mos"])
print(f"scored {sum(len(v) for v in clips.values())} clips; best source MOS {best[0]['mos']:.2f}", flush=True)
# Fill both pitch groups evenly, best source quality first.
cands, counts = [], {"high": 0, "low": 0}
for c in best:
    c["pitch"] = median_pitch(c["wav"], c["sr"])
    g = "high" if c["pitch"] >= HIGH_PITCH_HZ else "low"
    if counts[g] < CANDIDATES_PER_GROUP:
        counts[g] += 1
        cands.append(c)
    if all(v >= CANDIDATES_PER_GROUP for v in counts.values()):
        break
print(f"candidates: {counts}", flush=True)

# 3. clone each candidate and score the cloned speech (Pocket TTS on CPU, like production)
tts = TTSModel.load_model()
tmp = tempfile.mkdtemp()
results = []
for i, c in enumerate(cands):
    ref = os.path.join(tmp, f"{c['speaker']}.wav")
    sf.write(ref, c["wav"], c["sr"])
    try:
        state = tts.get_state_for_audio_prompt(ref)
        outs, scores, ok = [], [], True
        for line in TEST_LINES:
            a = tts.generate_audio(state, line).numpy()
            sec = len(a) / tts.sample_rate
            rate = len(line) / max(sec, 0.1)
            if not (9 <= rate <= 22):  # chars/second: catches cut-offs and rambling
                ok = False
            outs.append(a)
            scores.append(utmos(a, tts.sample_rate))
    except Exception as e:  # noqa: BLE001
        print(f"  skip {c['speaker']}: {e}", flush=True)
        continue
    c.update(state=state, outs=outs, clone_mos=float(np.mean(scores)), ok=ok)
    results.append(c)
    print(f"[{i+1}/{len(cands)}] spk {c['speaker']} pitch {c['pitch']:.0f}Hz "
          f"src {c['mos']:.2f} clone {c['clone_mos']:.2f} ok={ok}", flush=True)

# 4. pick winners per pitch group and export
os.makedirs(os.path.join(OUT, "refs"), exist_ok=True)
catalog = []
for group in ("high", "low"):
    pool = [r for r in results if r["ok"] and ((r["pitch"] >= HIGH_PITCH_HZ) == (group == "high"))]
    pool.sort(key=lambda r: -(r["clone_mos"] * 0.7 + r["mos"] * 0.3))
    for name, r in zip(NAMES[group], pool[:FINAL_PER_GROUP]):
        export_model_state(r["state"], os.path.join(OUT, f"{name}.safetensors"))
        sf.write(os.path.join(OUT, "refs", f"{name}.wav"), r["wav"], r["sr"])
        demo = np.concatenate([r["outs"][0], np.zeros(int(0.4 * tts.sample_rate), np.float32), r["outs"][1]])
        sf.write(os.path.join(OUT, f"{name}.mp3"), demo, tts.sample_rate, format="MP3")
        catalog.append({
            "id": name,
            "pitch": group,
            "pitch_hz": round(r["pitch"]),
            "quality": round(r["clone_mos"], 2),
            "pace": round(sum(len(t) for t in TEST_LINES) / sum(len(o) / tts.sample_rate for o in r["outs"]), 1),
            "source": {
                "dataset": "LibriTTS-R",
                "license": "CC BY 4.0",
                "speaker_id": r["speaker"],
                "utterance_id": r["id"],
                "text": r["text"],
            },
        })
        print(f"picked {name}: spk {r['speaker']} clone {r['clone_mos']:.2f}", flush=True)

with open(os.path.join(OUT, "catalog.json"), "w") as f:
    json.dump({"model": "kyutai/pocket-tts", "sample_text": TEST_LINES, "voices": catalog}, f, indent=2)
print(f"done: {len(catalog)} voices in {time.time()-t0:.0f}s", flush=True)
