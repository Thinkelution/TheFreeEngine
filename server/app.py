"""TheFreeEngine speech API: Pocket TTS on CPU with API keys and daily limits."""
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
import torch
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pocket_tts import TTSModel, export_model_state
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TFE_DATA", REPO / "data"))
VOICES = Path(os.environ.get("TFE_VOICES", REPO / "voices"))
MAX_CHARS = int(os.environ.get("TFE_MAX_CHARS", "1000"))
DAILY_CHARS = int(os.environ.get("TFE_DAILY_CHARS", "20000"))
DAILY_CLONES = int(os.environ.get("TFE_DAILY_CLONES", "3"))
MAX_USER_VOICES = int(os.environ.get("TFE_MAX_USER_VOICES", "10"))
WORKERS = int(os.environ.get("TFE_WORKERS", "2"))
MAX_UPLOAD = 5 * 1024 * 1024
VOICE_ID = re.compile(r"^[a-z0-9_]{1,40}$")

DATA.mkdir(parents=True, exist_ok=True)
(DATA / "voices").mkdir(exist_ok=True)
_db_lock = threading.Lock()
_db = sqlite3.connect(DATA / "tfe.db", check_same_thread=False)
_db.executescript("""
CREATE TABLE IF NOT EXISTS keys(hash TEXT PRIMARY KEY, name TEXT, created TEXT, disabled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage(hash TEXT, day TEXT, chars INTEGER DEFAULT 0, clones INTEGER DEFAULT 0,
                                 PRIMARY KEY(hash, day));
CREATE TABLE IF NOT EXISTS user_voices(id TEXT PRIMARY KEY, hash TEXT, name TEXT, created TEXT);
""")

state = {}
gate = asyncio.Semaphore(WORKERS)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def today() -> str:
    return dt.date.today().isoformat()


def usage(h: str) -> tuple[int, int]:
    row = _db.execute("SELECT chars, clones FROM usage WHERE hash=? AND day=?", (h, today())).fetchone()
    return row or (0, 0)


def charge(h: str, chars: int = 0, clones: int = 0) -> None:
    with _db_lock, _db:
        _db.execute("INSERT INTO usage(hash, day) VALUES(?, ?) ON CONFLICT DO NOTHING", (h, today()))
        _db.execute("UPDATE usage SET chars=chars+?, clones=clones+? WHERE hash=? AND day=?",
                    (chars, clones, h, today()))


def auth(authorization: str = Header(default="")) -> str:
    key = authorization.removeprefix("Bearer ").strip()
    if not key:
        raise HTTPException(401, "Missing API key. Send 'Authorization: Bearer <key>'.")
    h = key_hash(key)
    row = _db.execute("SELECT disabled FROM keys WHERE hash=?", (h,)).fetchone()
    if not row or row[0]:
        raise HTTPException(401, "Invalid API key.")
    return h


def load_catalog() -> dict:
    path = VOICES / "catalog.json"
    if not path.exists():
        return {}
    return {v["id"]: v for v in json.loads(path.read_text())["voices"]}


@asynccontextmanager
async def lifespan(_: FastAPI):
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // WORKERS))
    state["model"] = TTSModel.load_model()
    state["catalog"] = load_catalog()
    state["voices"] = {}
    for vid in state["catalog"]:
        state["voices"][vid] = state["model"].get_state_for_audio_prompt(str(VOICES / f"{vid}.safetensors"))
    # Optional Kyutai catalog voices, e.g. TFE_BUILTIN_VOICES=alba,marius (check each voice's license).
    for vid in filter(None, os.environ.get("TFE_BUILTIN_VOICES", "").split(",")):
        state["voices"][vid] = state["model"].get_state_for_audio_prompt(vid)
        state["catalog"][vid] = {"id": vid, "pitch": "", "quality": None}
    yield


app = FastAPI(title="TheFreeEngine", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def voice_state(vid: str, h: str):
    if vid in state["voices"]:
        return state["voices"][vid]
    if vid.startswith("u_") and VOICE_ID.match(vid):
        row = _db.execute("SELECT 1 FROM user_voices WHERE id=? AND hash=?", (vid, h)).fetchone()
        path = DATA / "voices" / f"{vid}.safetensors"
        if row and path.exists():
            return state["model"].get_state_for_audio_prompt(str(path))
    raise HTTPException(404, f"Unknown voice '{vid}'. See GET /v1/voices.")


def encode(audio: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str]:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        wavfile.write(f.name, sr, pcm)
        if fmt == "wav":
            return Path(f.name).read_bytes(), "audio/wav"
        mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", f.name, "-b:a", "96k", "-f", "mp3", "-"],
                             capture_output=True, check=True).stdout
        return mp3, "audio/mpeg"


@app.get("/v1/health")
def health():
    return {"ok": True, "voices": len(state.get("catalog", {}))}


@app.get("/v1/voices")
def voices(h: str = Depends(auth)):
    house = [{"id": v["id"], "pitch": v["pitch"], "quality": v["quality"], "type": "house"}
             for v in state["catalog"].values()]
    mine = [{"id": r[0], "name": r[1], "type": "cloned"}
            for r in _db.execute("SELECT id, name FROM user_voices WHERE hash=?", (h,))]
    return {"voices": house + mine}


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = "aria"
    format: str = Field(default="mp3", pattern="^(mp3|wav)$")


@app.post("/v1/tts")
async def tts(req: TTSRequest, h: str = Depends(auth)):
    text = req.text.strip()
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"Text is over {MAX_CHARS} characters.")
    chars, _ = usage(h)
    if chars + len(text) > DAILY_CHARS:
        raise HTTPException(429, f"Daily limit of {DAILY_CHARS} characters reached.")
    vs = voice_state(req.voice, h)
    async with gate:
        audio = await asyncio.to_thread(lambda: state["model"].generate_audio(vs, text).numpy())
    charge(h, chars=len(text))
    body, mime = await asyncio.to_thread(encode, audio, state["model"].sample_rate, req.format)
    return Response(body, media_type=mime)


@app.post("/v1/voices/clone")
async def clone(file: UploadFile = File(...), name: str = Form(...), consent: str = Form(""),
                h: str = Depends(auth)):
    if consent.lower() != "yes":
        raise HTTPException(400, "Set consent=yes to confirm you have the speaker's permission.")
    _, clones = usage(h)
    if clones >= DAILY_CLONES:
        raise HTTPException(429, f"Daily limit of {DAILY_CLONES} clones reached.")
    count = _db.execute("SELECT COUNT(*) FROM user_voices WHERE hash=?", (h,)).fetchone()[0]
    if count >= MAX_USER_VOICES:
        raise HTTPException(409, f"You already have {MAX_USER_VOICES} voices.")
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Audio file is over 5 MB.")
    vid = "u_" + uuid.uuid4().hex[:16]
    with tempfile.TemporaryDirectory() as d:
        src, wav = Path(d) / "in", Path(d) / "ref.wav"
        src.write_bytes(raw)
        # Normalize to 24 kHz mono, trim to 20 s. ffmpeg rejects non-audio input.
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-t", "20", "-ac", "1",
                            "-ar", "24000", str(wav)], capture_output=True)
        if r.returncode != 0:
            raise HTTPException(400, "Could not read that audio file.")
        async with gate:
            try:
                vs = await asyncio.to_thread(state["model"].get_state_for_audio_prompt, str(wav))
            except ValueError as e:
                raise HTTPException(503, "Voice cloning is not enabled on this server.") from e
        export_model_state(vs, str(DATA / "voices" / f"{vid}.safetensors"))
    with _db_lock, _db:
        _db.execute("INSERT INTO user_voices VALUES(?, ?, ?, ?)", (vid, h, name[:60], dt.datetime.utcnow().isoformat()))
    charge(h, clones=1)
    return {"voice": vid, "name": name[:60]}


@app.get("/v1/usage")
def get_usage(h: str = Depends(auth)):
    chars, clones = usage(h)
    return {"day": today(), "chars": chars, "chars_limit": DAILY_CHARS,
            "clones": clones, "clones_limit": DAILY_CLONES, "max_chars_per_request": MAX_CHARS}
