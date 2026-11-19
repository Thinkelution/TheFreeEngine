"""Music worker: takes queued tracks from the shared SQLite DB, makes them with
Stable Audio 3 Small (CPU, float32, 1 thread), saves MP3, emails the owner.

Runs as its own systemd service (freeengine-music) with a memory cap, so a
problem here cannot take down the web app or other sites.
"""
import datetime as dt
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

# The 208 CPU (no AVX2) crashes with fp16 or 4+ threads; 1 thread is as fast as 2 there.
torch.set_num_threads(int(os.environ.get("TFE_MUSIC_THREADS", "1")))
from stable_audio_3 import StableAudioModel  # noqa: E402

log = logging.getLogger("tfe-music")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
DATA = Path(os.environ.get("TFE_DATA", "data"))
BASE_URL = os.environ.get("TFE_BASE_URL", "http://127.0.0.1:8710").rstrip("/")
MAIL_FROM = os.environ.get("TFE_MAIL_FROM", "vivek@thinkelution.com")
MAIL_FROM_NAME = os.environ.get("TFE_MAIL_FROM_NAME", "The Free Engine")
MAILERSEND_KEY = os.environ.get("MAILERSEND_API_KEY", "")
MAIL_LOG = os.environ.get("TFE_MAIL_LOG", "")
OUT = DATA / "music"
OUT.mkdir(parents=True, exist_ok=True)

db = sqlite3.connect(DATA / "tfe.db", timeout=30, isolation_level=None)
db.row_factory = sqlite3.Row
db.execute("PRAGMA busy_timeout=30000")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def send_email(to, subject, text, html):
    if MAIL_LOG:
        with open(MAIL_LOG, "a") as f:
            f.write(json.dumps({"to": to, "subject": subject, "text": text}) + "\n")
        return
    if not MAILERSEND_KEY:
        log.warning("no MAILERSEND_API_KEY; email to %s skipped", to)
        return
    body = json.dumps({"from": {"email": MAIL_FROM, "name": MAIL_FROM_NAME}, "to": [{"email": to}],
                       "subject": subject, "text": text, "html": html}).encode()
    req = urllib.request.Request("https://api.mailersend.com/v1/email", data=body, method="POST", headers={
        "Authorization": f"Bearer {MAILERSEND_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log.info("email to %s: %s", to, r.status)
    except Exception as e:  # noqa: BLE001
        log.error("email to %s failed: %s", to, e)


def html(title, lines, button=None):
    btn = (f'<p style="margin:28px 0"><a href="{button[1]}" style="background:#7c5cff;color:#fff;padding:12px 22px;'
           f'border-radius:10px;text-decoration:none;font-weight:600">{button[0]}</a></p>' if button else "")
    return (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:520px;margin:auto;padding:32px;'
            f'color:#1d1d29"><h2>{title}</h2>' + "".join(f"<p>{x}</p>" for x in lines) + btn +
            f'<p style="color:#8a8aa0;font-size:12px;margin-top:32px">The Free Engine · Music powered by Stability AI</p></div>')


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


log.info("loading Stable Audio 3 small-music (threads=%d)", torch.get_num_threads())
model = StableAudioModel.from_pretrained("small-music", device="cpu", model_half=False)
SR = getattr(model, "sample_rate", 44100)
db.execute("UPDATE music SET status='queued', started=NULL WHERE status='processing'")
log.info("ready")

while True:
    row = db.execute("SELECT m.*, u.email FROM music m JOIN users u ON u.id=m.owner_id "
                     "WHERE m.status='queued' ORDER BY m.created LIMIT 1").fetchone()
    if not row:
        time.sleep(5)
        continue
    mid = row["id"]
    db.execute("UPDATE music SET status='processing', started=? WHERE id=?", (now_iso(), mid))
    t0 = time.time()
    try:
        with torch.inference_mode():
            audio = model.generate(prompt=row["prompt"], duration=int(row["duration"]))
        x = np.squeeze(audio.detach().float().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio))
        if x.ndim == 2 and x.shape[0] < x.shape[1]:
            x = x.T
        x = x / max(float(np.abs(x).max()), 1e-6) * 0.95
        with tempfile.TemporaryDirectory() as d:
            w = os.path.join(d, "a.wav")
            sf.write(w, x, SR)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", w, "-b:a", "160k",
                            str(OUT / f"{mid}.mp3")], check=True)
        db.execute("UPDATE music SET status='ready', finished=?, error=NULL WHERE id=?", (now_iso(), mid))
        log.info("made %s (%ss) in %.0fs", mid, row["duration"], time.time() - t0)
        link = f"{BASE_URL}/#/music"
        send_email(row["email"], "Your music track is ready",
                   f"Your track is ready: \"{row['prompt'][:80]}\"\nListen: {link}",
                   html("Your track is ready 🎵", [f"<i>{esc(row['prompt'][:120])}</i>",
                                                   f"{row['duration']} seconds, ready to play and download."],
                        ("Listen now", link)))
    except Exception as e:  # noqa: BLE001
        log.exception("music %s failed", mid)
        db.execute("UPDATE music SET status='failed', error=?, finished=? WHERE id=?", (str(e)[:300], now_iso(), mid))
        day = row["created"][:10]
        db.execute("UPDATE usage SET music=MAX(0, music-1) WHERE user_id=? AND day=?", (row["owner_id"], day))
        send_email(row["email"], "We could not make your track",
                   "Sorry, your music track failed. It did not count against your daily limit. Please try again.",
                   html("Your track did not work", ["Sorry, something went wrong making your track.",
                                                    "It did not count against your daily limit. Please try again."]))
