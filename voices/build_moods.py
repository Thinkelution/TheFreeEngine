"""Add mood versions, tone, pace and gender to the 60 house voices.

For every house voice (a LibriTTS-R speaker, CC BY 4.0):
1. Collect up to MAX_CLIPS of that speaker's clips (3.5-14 s).
2. Score each clip: emotion (emotion2vec+, 9 classes) plus arousal from energy,
   pitch movement and speaking rate.
3. For each mood, take the best few clips, clone them with Pocket TTS, speak a
   test line, and score the *output* the same way. Keep a mood only when the
   output shows it clearly more than the neutral voice reading the same line.
4. Write <id>@<mood>.safetensors, <id>@<mood>.mp3 and an updated catalog.json.

Moods: neutral (the existing voice), happy, sad, excited, calm, serious.
"""
import io
import json
import os
import sys
import tempfile
import time
import urllib.request
import tarfile
from collections import defaultdict

import librosa
import numpy as np
import soundfile as sf
import torch
from datasets import Audio, load_dataset
from pocket_tts import TTSModel, export_model_state

SRC = sys.argv[1]            # folder with catalog.json + <id>.safetensors (neutral)
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SPLITS = ["test.clean", "dev.clean", "train.clean.100", "train.clean.360"]
MAX_CLIPS = int(os.environ.get("TFE_MAX_CLIPS", "160"))
TRY_PER_MOOD = int(os.environ.get("TFE_TRY_PER_MOOD", "3"))
DEV = "cuda" if torch.cuda.is_available() else "cpu"
MOODS = ["happy", "sad", "excited", "calm", "serious"]
LINES = {
    "neutral": "Here is the plan for today. We start at nine, and the meeting ends at noon.",
    "happy": "Oh, this is wonderful news! I'm so glad it all worked out for you.",
    "sad": "I'm sorry. I really thought we would see each other one more time.",
    "excited": "We did it! I can't believe it, this is the best day ever!",
    "calm": "Take a slow breath in, and let it go. There is nothing you need to do right now.",
    "serious": "Listen carefully. This is important, and I need you to follow every step.",
}
E9 = ["angry", "disgusted", "fearful", "happy", "neutral", "other", "sad", "surprised", "unknown"]
t0 = time.time()
log = lambda *a: print(f"[{time.time()-t0:5.0f}s]", *a, flush=True)  # noqa: E731

cat = json.load(open(os.path.join(SRC, "catalog.json")))
voices = {v["source"]["speaker_id"]: v for v in cat["voices"][: int(os.environ.get("TFE_LIMIT_VOICES", "999"))]}
log(f"device={DEV} voices={len(voices)}")

# ---------------------------------------------------------------- gender (LibriSpeech SPEAKERS.TXT)
gender = {}
try:
    raw = urllib.request.urlopen("https://www.openslr.org/resources/12/raw-metadata.tar.gz", timeout=60).read()
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf.getmembers():
            if m.name.endswith("SPEAKERS.TXT"):
                for line in tf.extractfile(m).read().decode("utf-8", "replace").splitlines():
                    if line.startswith(";"):
                        continue
                    p = [x.strip() for x in line.split("|")]
                    if len(p) > 1 and p[1] in ("F", "M"):
                        gender[p[0]] = p[1].lower()
    log(f"gender for {len(gender)} speakers")
except Exception as e:  # noqa: BLE001
    log("no SPEAKERS.TXT, gender from pitch:", e)

# ---------------------------------------------------------------- emotion model
from funasr import AutoModel  # noqa: E402

ser = AutoModel(model="iic/emotion2vec_plus_large", hub="hf" if os.environ.get("TFE_SER_HUB") == "hf" else "ms",
                device=DEV, disable_update=True)


def emotions(wav: np.ndarray, sr: int) -> dict:
    x = librosa.resample(wav.astype(np.float32), orig_sr=sr, target_sr=16000) if sr != 16000 else wav.astype(np.float32)
    r = ser.generate(x, granularity="utterance", extract_embedding=False, disable_pbar=True)[0]
    out = {}
    for lab, sc in zip(r["labels"], r["scores"]):
        k = lab.split("/")[-1].strip().lower()
        out[k] = float(sc)
    return {k: out.get(k, 0.0) for k in E9}


def prosody(wav: np.ndarray, sr: int, text_len: int) -> dict:
    y = librosa.resample(wav, orig_sr=sr, target_sr=16000) if sr != 16000 else wav
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=320)[0]
    f0 = librosa.yin(y, fmin=60, fmax=450, sr=16000, frame_length=1024, hop_length=320)
    n = min(len(rms), len(f0))
    voiced = rms[:n] > np.percentile(rms[:n], 50)
    f = f0[:n][voiced]
    semis = 12 * np.log2(f / max(np.median(f), 1)) if len(f) else np.zeros(1)
    cent = librosa.feature.spectral_centroid(y=y, sr=16000)[0]
    return {
        "energy": float(20 * np.log10(np.mean(rms[voiced]) + 1e-6)) if voiced.any() else -60.0,
        "pitch_var": float(np.std(semis)),
        "pitch_hz": float(np.median(f)) if len(f) else 0.0,
        "rate": text_len / (len(y) / 16000),
        "centroid": float(np.median(cent)),
    }


# mood score from emotion probabilities + prosody z-scores (per speaker)
def mood_scores(e: dict, z: dict) -> dict:
    arousal = 0.5 * z["energy"] + 0.35 * z["pitch_var"] + 0.25 * z["rate"]
    return {
        "happy": e["happy"] + 0.1 * max(arousal, 0),
        "sad": e["sad"] - 0.05 * arousal,
        "excited": 0.6 * (e["happy"] + e["surprised"]) + 0.15 * arousal,
        "calm": e["neutral"] - 0.2 * arousal - 0.5 * (e["angry"] + e["sad"] + e["fearful"]),
        "serious": e["angry"] + 0.5 * e["disgusted"] + 0.05 * max(arousal, 0) - 0.5 * e["happy"],
    }


# ---------------------------------------------------------------- 1. collect clips
clips = defaultdict(list)
need = set(voices)
for split in SPLITS:
    if not need:
        break
    ds = load_dataset("mythicinfinity/libritts_r", "clean", split=split, streaming=True).cast_column("audio", Audio(decode=False))
    seen = 0
    for row in ds:
        spk = str(row["speaker_id"])
        if spk not in voices or len(clips[spk]) >= MAX_CLIPS:
            continue
        wav, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        wav = wav.mean(axis=1) if wav.ndim > 1 else wav
        if not 3.5 <= len(wav) / sr <= 14:
            continue
        clips[spk].append({"id": row["id"], "text": row["text_normalized"], "wav": wav, "sr": sr})
        seen += 1
        if len(clips[spk]) >= MAX_CLIPS:
            need.discard(spk)
    log(f"{split}: +{seen} clips, {sum(len(v) for v in clips.values())} total, {len(need)} speakers still filling")

# ---------------------------------------------------------------- 2. score clips
for spk, cs in clips.items():
    for c in cs:
        c["emo"] = emotions(c["wav"], c["sr"])
        c["pro"] = prosody(c["wav"], c["sr"], len(c["text"]))
    keys = ["energy", "pitch_var", "rate"]
    mu = {k: np.mean([c["pro"][k] for c in cs]) for k in keys}
    sd = {k: np.std([c["pro"][k] for c in cs]) + 1e-6 for k in keys}
    for c in cs:
        c["z"] = {k: (c["pro"][k] - mu[k]) / sd[k] for k in keys}
        c["ms"] = mood_scores(c["emo"], c["z"])
log("scored all clips")

# ---------------------------------------------------------------- 3. clone, speak, verify
cpu_tts = TTSModel.load_model()
gpu_tts = TTSModel.load_model()
if DEV == "cuda":
    gpu_tts.to("cuda")
SR = cpu_tts.sample_rate
tmp = tempfile.mkdtemp()


def speak(state_path: str, text: str) -> np.ndarray:
    st = gpu_tts.get_state_for_audio_prompt(state_path)
    return gpu_tts.generate_audio(st, text).detach().float().cpu().numpy()


def out_mood(audio: np.ndarray, line: str, base_z: dict) -> dict:
    e = emotions(audio, SR)
    p = prosody(audio, SR, len(line))
    z = {k: (p[k] - base_z[k][0]) / base_z[k][1] for k in ("energy", "pitch_var", "rate")}
    return mood_scores(e, z)


mos_model = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True).to(DEV).eval()


def utmos(a):
    with torch.no_grad():
        return float(mos_model(torch.as_tensor(a, dtype=torch.float32, device=DEV)[None], SR).item())


new_cat = []
for spk, v in voices.items():
    vid, cs = v["id"], clips.get(spk, [])
    neutral_path = os.path.join(SRC, f"{vid}.safetensors")
    # reference prosody of TTS output from the neutral voice, for the output z-scores
    ref = {m: speak(neutral_path, LINES[m]) for m in LINES}
    rp = {m: prosody(a, SR, len(LINES[m])) for m, a in ref.items()}
    base_z = {k: (np.mean([rp[m][k] for m in rp]), np.std([rp[m][k] for m in rp]) + 1e-6) for k in ("energy", "pitch_var", "rate")}
    neutral_ms = {m: out_mood(ref[m], LINES[m], base_z)[m] for m in MOODS}
    pro = prosody(np.concatenate([c["wav"] for c in cs[:20]]) if cs else ref["neutral"], cs[0]["sr"] if cs else SR, 1)
    entry = dict(v)
    entry["gender"] = gender.get(spk) or ("f" if v["pitch"] == "high" else "m")
    entry["centroid"] = round(pro["centroid"])
    entry["moods"] = {"neutral": {"quality": v["quality"]}}
    for mood in MOODS:
        best = None
        for c in sorted(cs, key=lambda c: -c["ms"][mood])[:TRY_PER_MOOD]:
            ref_wav = os.path.join(tmp, "ref.wav")
            sf.write(ref_wav, c["wav"], c["sr"])
            st = cpu_tts.get_state_for_audio_prompt(ref_wav)
            sp = os.path.join(tmp, f"{vid}@{mood}.safetensors")
            export_model_state(st, sp)
            a = speak(sp, LINES[mood])
            secs = len(a) / SR
            if not 7 <= len(LINES[mood]) / max(secs, .1) <= 24:
                continue
            gain = out_mood(a, LINES[mood], base_z)[mood] - neutral_ms[mood]
            q = utmos(a)
            score = gain + 0.05 * (q - 4)
            if q >= 3.6 and gain >= 0.12 and (best is None or score > best[0]):
                best = (score, gain, q, c, a, sp)
        if best:
            score, gain, q, c, a, sp = best
            os.replace(sp, os.path.join(OUT, f"{vid}@{mood}.safetensors"))
            sf.write(os.path.join(OUT, f"{vid}@{mood}.mp3"), a / max(np.abs(a).max(), 1e-6) * 0.95, SR, format="MP3")
            entry["moods"][mood] = {"quality": round(q, 2), "strength": round(gain, 2),
                                    "source": {"utterance_id": c["id"], "text": c["text"]}}
    new_cat.append(entry)
    log(f"{vid}: {entry['gender']} moods={list(entry['moods'])}")

# ---------------------------------------------------------------- 4. tone + pitch bands, write catalog
cents = np.array([e["centroid"] for e in new_cat])
hz = np.array([e["pitch_hz"] for e in new_cat])
for e in new_cat:
    same = [x for x in new_cat if x["gender"] == e["gender"]]
    c_rank = np.mean([x["centroid"] <= e["centroid"] for x in same])
    p_rank = np.mean([x["pitch_hz"] <= e["pitch_hz"] for x in same])
    e["pitch_band"] = "high" if p_rank > 0.67 else "low" if p_rank <= 0.33 else "mid"
    if p_rank <= 0.33 and c_rank <= 0.5:
        e["tone"] = "deep"
    elif c_rank > 0.67:
        e["tone"] = "bright"
    elif c_rank <= 0.33:
        e["tone"] = "warm"
    else:
        e["tone"] = "smooth"
    e["pace_band"] = "fast" if e.get("pace", 15) >= 16.5 else "slow" if e.get("pace", 15) < 13.5 else "normal"
for e in new_cat:
    e.pop("centroid", None)
cat["voices"] = new_cat
cat["moods"] = ["neutral"] + MOODS
cat["mood_lines"] = LINES
json.dump(cat, open(os.path.join(OUT, "catalog.json"), "w"), indent=2)
counts = defaultdict(int)
for e in new_cat:
    for m in e["moods"]:
        counts[m] += 1
log("done", dict(counts))
