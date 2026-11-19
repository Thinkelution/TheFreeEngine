"""TheFreeEngine: free text-to-speech and voice cloning on CPU (Kyutai Pocket TTS).

Accounts (email + password, email verification), browser sessions, API keys,
daily limits, a queued voice-cloning worker with email notification,
public/private cloned voices, and an admin area limited to ADMIN_EMAILS.
"""
import asyncio
import collections
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
import torch
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pocket_tts import TTSModel, export_model_state
from pydantic import BaseModel, Field

log = logging.getLogger("tfe")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TFE_DATA", REPO / "data"))
VOICES = Path(os.environ.get("TFE_VOICES", REPO / "voices"))
BASE_URL = os.environ.get("TFE_BASE_URL", "http://127.0.0.1:8710").rstrip("/")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("TFE_ADMIN_EMAILS", "").split(",") if e.strip()}
MAIL_FROM = os.environ.get("TFE_MAIL_FROM", "vivek@thinkelution.com")
MAIL_FROM_NAME = os.environ.get("TFE_MAIL_FROM_NAME", "The Free Engine")
MAILERSEND_KEY = os.environ.get("MAILERSEND_API_KEY", "")
MAIL_LOG = os.environ.get("TFE_MAIL_LOG", "")  # dev/test: write emails to this file instead of sending
COOKIE_SECURE = os.environ.get("TFE_COOKIE_SECURE", "1") == "1"
MAX_CHARS = int(os.environ.get("TFE_MAX_CHARS", "1000"))
DAILY_CHARS = int(os.environ.get("TFE_DAILY_CHARS", "5000"))
CLONES_PER_ACCOUNT = int(os.environ.get("TFE_CLONES_PER_ACCOUNT", "1"))  # lifetime, free plan
DAILY_MUSIC = int(os.environ.get("TFE_DAILY_MUSIC", "3"))
MUSIC_MAX_SEC = int(os.environ.get("TFE_MUSIC_MAX_SEC", "30"))
MAX_USER_VOICES = int(os.environ.get("TFE_MAX_USER_VOICES", "10"))
MAX_KEYS = 5
WORKERS = int(os.environ.get("TFE_WORKERS", "2"))
MAX_UPLOAD = 10 * 1024 * 1024
SESSION_DAYS = 30
SAMPLE_TEXT = "Hi there! This is my new voice on The Free Engine. Pretty good, right?"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[a-z]{2,}$", re.I)
VOICE_ID = re.compile(r"^[a-z0-9_]{1,40}$")

for sub in ("", "voices", "samples", "uploads"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- database
_db_lock = threading.RLock()
_db = sqlite3.connect(DATA / "tfe.db", check_same_thread=False, isolation_level=None)
_db.row_factory = sqlite3.Row
_db.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, verified INTEGER DEFAULT 0,
  disabled INTEGER DEFAULT 0, created TEXT NOT NULL, last_login TEXT);
CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS api_keys(
  id INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, name TEXT, prefix TEXT,
  created TEXT NOT NULL, last_used TEXT, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage(user_id INTEGER, day TEXT, chars INTEGER DEFAULT 0, clones INTEGER DEFAULT 0,
  requests INTEGER DEFAULT 0, PRIMARY KEY(user_id, day));
CREATE TABLE IF NOT EXISTS voices(
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT DEFAULT '',
  visibility TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'queued', error TEXT,
  hidden INTEGER DEFAULT 0, created TEXT NOT NULL, finished TEXT);
""")


def q(sql, args=()):
    with _db_lock:
        return _db.execute(sql, args).fetchall()


def q1(sql, args=()):
    with _db_lock:
        return _db.execute(sql, args).fetchone()


def ex(sql, args=()):
    with _db_lock:
        return _db.execute(sql, args)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except Exception:  # noqa: BLE001
        return False


def new_token(user_id: int, kind: str, hours: float) -> str:
    t = secrets.token_urlsafe(32)
    ex("DELETE FROM tokens WHERE user_id=? AND kind=?", (user_id, kind))
    ex("INSERT INTO tokens VALUES(?,?,?,?)", (sha(t), user_id, kind, time.time() + hours * 3600))
    return t


def use_token(t: str, kind: str):
    row = q1("SELECT user_id, expires FROM tokens WHERE hash=? AND kind=?", (sha(t), kind))
    if not row or row["expires"] < time.time():
        return None
    ex("DELETE FROM tokens WHERE hash=?", (sha(t),))
    return row["user_id"]


_db.executescript("""
CREATE TABLE IF NOT EXISTS music(
  id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, prompt TEXT NOT NULL, duration INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', error TEXT, created TEXT NOT NULL, started TEXT, finished TEXT);
""")
for _sql in ("ALTER TABLE users ADD COLUMN clones_used INTEGER DEFAULT 0",
             "ALTER TABLE usage ADD COLUMN music INTEGER DEFAULT 0"):
    try:
        _db.execute(_sql)
    except sqlite3.OperationalError:
        pass  # column already there
(DATA / "music").mkdir(exist_ok=True)


def usage(uid: int) -> sqlite3.Row:
    ex("INSERT OR IGNORE INTO usage(user_id, day) VALUES(?,?)", (uid, today()))
    return q1("SELECT u.chars, u.clones, u.requests, u.music, a.clones_used FROM usage u JOIN users a ON a.id=u.user_id "
              "WHERE u.user_id=? AND u.day=?", (uid, today()))


def charge(uid: int, chars=0, clones=0, music=0):
    usage(uid)
    ex("UPDATE usage SET chars=chars+?, clones=clones+?, music=music+?, requests=requests+1 WHERE user_id=? AND day=?",
       (chars, clones, music, uid, today()))
    if clones:
        ex("UPDATE users SET clones_used=clones_used+? WHERE id=?", (clones, uid))


def usage_out(us) -> dict:
    return {"chars": us["chars"], "chars_limit": DAILY_CHARS, "max_chars_per_request": MAX_CHARS,
            "clones_used": us["clones_used"], "clones_limit": CLONES_PER_ACCOUNT,
            "music": us["music"], "music_limit": DAILY_MUSIC, "music_max_sec": MUSIC_MAX_SEC,
            "max_voices": MAX_USER_VOICES, "plan": "free"}


# ---------------------------------------------------------------- email
def send_email(to: str, subject: str, text: str, html: str):
    if MAIL_LOG:
        with open(MAIL_LOG, "a") as f:
            f.write(json.dumps({"to": to, "subject": subject, "text": text}) + "\n")
        return
    if not MAILERSEND_KEY:
        log.warning("MAILERSEND_API_KEY not set; email to %s not sent", to)
        return
    body = json.dumps({
        "from": {"email": MAIL_FROM, "name": MAIL_FROM_NAME}, "to": [{"email": to}],
        "subject": subject, "text": text, "html": html,
    }).encode()
    req = urllib.request.Request("https://api.mailersend.com/v1/email", data=body, method="POST", headers={
        "Authorization": f"Bearer {MAILERSEND_KEY}", "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log.info("email '%s' to %s: %s", subject, to, r.status)
    except Exception as e:  # noqa: BLE001
        log.error("email '%s' to %s failed: %s", subject, to, e)


def email_html(title: str, lines: list[str], button: tuple[str, str] | None = None) -> str:
    btn = (f'<p style="margin:28px 0"><a href="{button[1]}" style="background:#7c5cff;color:#fff;'
           f'padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600">{button[0]}</a></p>'
           if button else "")
    body = "".join(f'<p style="margin:0 0 12px">{x}</p>' for x in lines)
    return (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:520px;margin:auto;'
            f'padding:32px;color:#1d1d29"><h2 style="margin:0 0 18px">{title}</h2>{body}{btn}'
            f'<p style="color:#8a8aa0;font-size:12px;margin-top:32px">The Free Engine · '
            f'<a href="{BASE_URL}" style="color:#8a8aa0">{BASE_URL}</a></p></div>')


def mail_async(*args):
    threading.Thread(target=send_email, args=args, daemon=True).start()


def send_verify(uid: int, email: str):
    t = new_token(uid, "verify", 48)
    link = f"{BASE_URL}/#/verify?token={t}"
    mail_async(email, "Confirm your email for The Free Engine",
               f"Confirm your email to start using The Free Engine:\n{link}\n\nThis link works for 48 hours.",
               email_html("Confirm your email", ["Welcome! Click below to confirm your email and get your free API key.",
                                                 "This link works for 48 hours."], ("Confirm email", link)))


# ---------------------------------------------------------------- rate limits
_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)


def limit(bucket: str, n: int, per_sec: int):
    now = time.time()
    d = _hits[bucket]
    while d and d[0] < now - per_sec:
        d.popleft()
    if len(d) >= n:
        raise HTTPException(429, "Too many tries. Please wait a bit and try again.")
    d.append(now)


def client_ip(req: Request) -> str:
    return req.headers.get("cf-connecting-ip") or req.headers.get("x-real-ip") or (req.client.host if req.client else "?")


# ---------------------------------------------------------------- auth deps
class User(dict):
    @property
    def id(self) -> int:
        return self["id"]

    @property
    def is_admin(self) -> bool:
        return self["email"] in ADMIN_EMAILS


def _session_user(req: Request):
    tok = req.cookies.get("tfe_session")
    if not tok:
        return None
    row = q1("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>?",
             (sha(tok), time.time()))
    if not row or row["disabled"] or not row["verified"]:
        return None
    return User(row)


def _csrf(req: Request):
    if req.method not in ("GET", "HEAD", "OPTIONS") and req.headers.get("x-tfe") != "1":
        raise HTTPException(403, "Missing X-TFE header.")


def session_user(req: Request) -> User:
    u = _session_user(req)
    if not u:
        raise HTTPException(401, "Please log in.")
    _csrf(req)
    return u


def admin_user(u: User = Depends(session_user)) -> User:
    if not u.is_admin:
        raise HTTPException(404, "Not found.")
    return u


def api_user(req: Request) -> User:
    """Bearer API key, or a logged-in browser session (playground)."""
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        key = auth[7:].strip()
        row = q1("SELECT u.*, k.id AS key_id FROM api_keys k JOIN users u ON u.id=k.user_id "
                 "WHERE k.hash=? AND k.revoked=0", (sha(key),))
        if not row or row["disabled"]:
            raise HTTPException(401, "Invalid API key.")
        ex("UPDATE api_keys SET last_used=? WHERE id=?", (now_iso(), row["key_id"]))
        return User(row)
    u = _session_user(req)
    if u:
        _csrf(req)
        return u
    raise HTTPException(401, "Send 'Authorization: Bearer <your API key>'. Get a free key at " + BASE_URL)


# ---------------------------------------------------------------- model
state: dict = {}
gate = threading.BoundedSemaphore(WORKERS)
_user_states: "collections.OrderedDict[str, object]" = collections.OrderedDict()


def load_catalog() -> dict:
    p = VOICES / "catalog.json"
    if not p.exists():
        return {}
    return {v["id"]: v for v in json.loads(p.read_text())["voices"]}


def voice_state_for(vid: str, u: User):
    if vid in state["house"]:
        return state["house"][vid]
    if not (vid.startswith("u_") and VOICE_ID.match(vid)):
        raise HTTPException(404, f"Unknown voice '{vid}'. See GET /v1/voices.")
    row = q1("SELECT owner_id, visibility, status, hidden FROM voices WHERE id=?", (vid,))
    if not row or row["status"] != "ready" or not (
            row["owner_id"] == u.id or (row["visibility"] == "public" and not row["hidden"])):
        raise HTTPException(404, f"Unknown voice '{vid}'.")
    if vid not in _user_states:
        _user_states[vid] = state["model"].get_state_for_audio_prompt(str(DATA / "voices" / f"{vid}.safetensors"))
        while len(_user_states) > 64:
            _user_states.popitem(last=False)
    _user_states.move_to_end(vid)
    return _user_states[vid]


def synth(vs, text: str) -> np.ndarray:
    with gate:
        return state["model"].generate_audio(vs, text).numpy()


def encode(audio: np.ndarray, sr: int, fmt: str) -> tuple[bytes, str]:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "a.wav")
        wavfile.write(w, sr, pcm)
        if fmt == "wav":
            return Path(w).read_bytes(), "audio/wav"
        mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", w, "-b:a", "96k", "-f", "mp3", "-"],
                             capture_output=True, check=True).stdout
        return mp3, "audio/mpeg"


# ---------------------------------------------------------------- clone worker
_wake = threading.Event()


def clone_worker():
    ex("UPDATE voices SET status='queued' WHERE status='processing'")
    while True:
        row = q1("SELECT v.*, u.email FROM voices v JOIN users u ON u.id=v.owner_id "
                 "WHERE v.status='queued' ORDER BY v.created LIMIT 1")
        if not row:
            _wake.wait(10)
            _wake.clear()
            continue
        vid = row["id"]
        ex("UPDATE voices SET status='processing' WHERE id=?", (vid,))
        ref = DATA / "uploads" / f"{vid}.wav"
        try:
            with gate:
                vs = state["model"].get_state_for_audio_prompt(str(ref))
            export_model_state(vs, str(DATA / "voices" / f"{vid}.safetensors"))
            audio = synth(vs, SAMPLE_TEXT)
            mp3, _ = encode(audio, state["model"].sample_rate, "mp3")
            (DATA / "samples" / f"{vid}.mp3").write_bytes(mp3)
            ex("UPDATE voices SET status='ready', finished=?, error=NULL WHERE id=?", (now_iso(), vid))
            ref.unlink(missing_ok=True)
            link = f"{BASE_URL}/#/studio"
            send_email(row["email"], f"Your voice \"{row['name']}\" is ready",
                       f"Your cloned voice \"{row['name']}\" is ready. Voice ID: {vid}\nTry it: {link}",
                       email_html("Your voice is ready 🎉",
                                  [f"Your cloned voice <b>{row['name']}</b> is ready to use.",
                                   f"Voice ID: <code>{vid}</code>",
                                   "It is <b>" + row["visibility"] + "</b>. You can change that any time."],
                                  ("Try it now", link)))
            log.info("cloned %s for user %s", vid, row["owner_id"])
        except Exception as e:  # noqa: BLE001
            log.exception("clone %s failed", vid)
            ex("UPDATE voices SET status='failed', error=?, finished=? WHERE id=?", (str(e)[:300], now_iso(), vid))
            ex("UPDATE users SET clones_used=MAX(0, clones_used-1) WHERE id=?", (row["owner_id"],))  # failed: give it back
            send_email(row["email"], f"We could not clone \"{row['name']}\"",
                       "Sorry, cloning failed. Try a clean recording of 10-20 seconds with one speaker.",
                       email_html("Cloning did not work", [
                           f"Sorry, we could not clone <b>{row['name']}</b>.",
                           "Try a clean 10 to 20 second recording of one person talking, with no music."]))


@asynccontextmanager
async def lifespan(_: FastAPI):
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // WORKERS))
    state["model"] = TTSModel.load_model()
    state["catalog"] = load_catalog()
    state["house"] = {vid: state["model"].get_state_for_audio_prompt(str(VOICES / f"{vid}.safetensors"))
                      for vid in state["catalog"]}
    log.info("loaded %d house voices", len(state["house"]))
    threading.Thread(target=clone_worker, daemon=True).start()
    yield


app = FastAPI(title="The Free Engine", version="1.0.0", lifespan=lifespan,
              docs_url="/v1/docs", redoc_url=None, openapi_url="/v1/openapi.json")


@app.exception_handler(HTTPException)
async def http_error(_: Request, e: HTTPException):
    return JSONResponse({"error": e.detail}, status_code=e.status_code)


# ---------------------------------------------------------------- auth routes
class Creds(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class EmailOnly(BaseModel):
    email: str = Field(max_length=320)


class TokenBody(BaseModel):
    token: str = Field(max_length=200)


class ResetBody(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


def _check_new_pw(pw: str):
    if len(pw) < 8:
        raise HTTPException(400, "Use at least 8 characters for your password.")


def _start_session(resp: Response, uid: int):
    tok = secrets.token_urlsafe(32)
    ex("INSERT INTO sessions VALUES(?,?,?)", (sha(tok), uid, time.time() + SESSION_DAYS * 86400))
    ex("DELETE FROM sessions WHERE expires<?", (time.time(),))
    ex("UPDATE users SET last_login=? WHERE id=?", (now_iso(), uid))
    resp.set_cookie("tfe_session", tok, max_age=SESSION_DAYS * 86400, httponly=True, secure=COOKIE_SECURE,
                    samesite="lax", path="/")


@app.post("/auth/signup")
def signup(body: Creds, req: Request):
    _csrf(req)
    limit("signup:" + client_ip(req), 5, 3600)
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "That email does not look right.")
    _check_new_pw(body.password)
    row = q1("SELECT id, verified FROM users WHERE email=?", (email,))
    if row and row["verified"]:
        raise HTTPException(409, "This email already has an account. Log in instead.")
    if row:
        ex("UPDATE users SET pw=? WHERE id=?", (hash_pw(body.password), row["id"]))
        uid = row["id"]
    else:
        uid = ex("INSERT INTO users(email, pw, created) VALUES(?,?,?)", (email, hash_pw(body.password), now_iso())).lastrowid
    send_verify(uid, email)
    return {"ok": True, "message": "Check your inbox to confirm your email."}


@app.post("/auth/resend")
def resend(body: EmailOnly, req: Request):
    _csrf(req)
    limit("resend:" + client_ip(req), 5, 3600)
    row = q1("SELECT id, email, verified FROM users WHERE email=?", (body.email.strip().lower(),))
    if row and not row["verified"]:
        send_verify(row["id"], row["email"])
    return {"ok": True}


@app.post("/auth/verify")
def verify(body: TokenBody, req: Request, resp: Response):
    _csrf(req)
    uid = use_token(body.token, "verify")
    if not uid:
        raise HTTPException(400, "This link has expired or was already used.")
    ex("UPDATE users SET verified=1 WHERE id=?", (uid,))
    _start_session(resp, uid)
    return {"ok": True}


@app.post("/auth/login")
def login(body: Creds, req: Request, resp: Response):
    _csrf(req)
    email = body.email.strip().lower()
    limit("login-ip:" + client_ip(req), 20, 600)
    limit("login:" + email, 8, 600)
    row = q1("SELECT * FROM users WHERE email=?", (email,))
    if not row or not check_pw(body.password, row["pw"]):
        raise HTTPException(401, "Wrong email or password.")
    if row["disabled"]:
        raise HTTPException(403, "This account is turned off.")
    if not row["verified"]:
        raise HTTPException(403, "Please confirm your email first. Check your inbox.")
    _start_session(resp, row["id"])
    return {"ok": True}


@app.post("/auth/logout")
def logout(req: Request, resp: Response):
    _csrf(req)
    tok = req.cookies.get("tfe_session")
    if tok:
        ex("DELETE FROM sessions WHERE hash=?", (sha(tok),))
    resp.delete_cookie("tfe_session", path="/")
    return {"ok": True}


@app.post("/auth/forgot")
def forgot(body: EmailOnly, req: Request):
    _csrf(req)
    limit("forgot:" + client_ip(req), 5, 3600)
    row = q1("SELECT id, email FROM users WHERE email=? AND verified=1", (body.email.strip().lower(),))
    if row:
        t = new_token(row["id"], "reset", 2)
        link = f"{BASE_URL}/#/reset?token={t}"
        mail_async(row["email"], "Reset your Free Engine password",
                   f"Reset your password: {link}\nThis link works for 2 hours. Ignore this if it was not you.",
                   email_html("Reset your password", ["Click below to pick a new password.",
                                                      "This link works for 2 hours. If this was not you, ignore it."],
                              ("Reset password", link)))
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


@app.post("/auth/reset")
def reset(body: ResetBody, req: Request, resp: Response):
    _csrf(req)
    _check_new_pw(body.password)
    uid = use_token(body.token, "reset")
    if not uid:
        raise HTTPException(400, "This link has expired or was already used.")
    ex("UPDATE users SET pw=? WHERE id=?", (hash_pw(body.password), uid))
    ex("DELETE FROM sessions WHERE user_id=?", (uid,))
    _start_session(resp, uid)
    return {"ok": True}


# ---------------------------------------------------------------- account routes
def _voice_out(r: sqlite3.Row, mine=False) -> dict:
    d = {"id": r["id"], "name": r["name"], "type": "community", "visibility": r["visibility"],
         "sample": f"/api/voices/{r['id']}/sample" if r["status"] == "ready" else None}
    if mine:
        d.update(status=r["status"], error=r["error"], created=r["created"], hidden=bool(r["hidden"]))
        if r["status"] == "queued":
            d["queue_position"] = q1("SELECT COUNT(*) n FROM voices WHERE status='queued' AND created<=?",
                                     (r["created"],))["n"]
    return d


@app.get("/api/me")
def me(u: User = Depends(session_user)):
    us = usage(u.id)
    return {"email": u["email"], "admin": u.is_admin, "created": u["created"],
            "usage": usage_out(us)}


@app.get("/api/keys")
def list_keys(u: User = Depends(session_user)):
    return {"keys": [dict(r) for r in q("SELECT id, name, prefix, created, last_used FROM api_keys "
                                         "WHERE user_id=? AND revoked=0 ORDER BY id", (u.id,))]}


class KeyBody(BaseModel):
    name: str = Field(default="My key", max_length=60)


@app.post("/api/keys")
def create_key(body: KeyBody, u: User = Depends(session_user)):
    n = q1("SELECT COUNT(*) n FROM api_keys WHERE user_id=? AND revoked=0", (u.id,))["n"]
    if n >= MAX_KEYS:
        raise HTTPException(409, f"You can have up to {MAX_KEYS} keys. Delete one first.")
    key = "tfe_" + secrets.token_urlsafe(30)
    ex("INSERT INTO api_keys(hash, user_id, name, prefix, created) VALUES(?,?,?,?,?)",
       (sha(key), u.id, body.name.strip() or "My key", key[:10], now_iso()))
    return {"key": key, "message": "Copy this key now. You will not see it again."}


@app.delete("/api/keys/{kid}")
def delete_key(kid: int, u: User = Depends(session_user)):
    ex("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (kid, u.id))
    return {"ok": True}


@app.get("/api/voices")
def public_voices():
    house = [{"id": v["id"], "name": v["id"].title(), "type": "house", "pitch": v.get("pitch"),
              "quality": v.get("quality"), "pace": v.get("pace"), "sample": f"/samples/{v['id']}.mp3"}
             for v in state["catalog"].values()]
    community = [_voice_out(r) for r in q("SELECT * FROM voices WHERE visibility='public' AND status='ready' "
                                          "AND hidden=0 ORDER BY finished DESC LIMIT 500")]
    return {"voices": house + community}


@app.get("/api/voices/{vid}/sample")
def voice_sample(vid: str, req: Request):
    row = q1("SELECT owner_id, visibility, hidden, status FROM voices WHERE id=?", (vid,))
    if not row or row["status"] != "ready":
        raise HTTPException(404, "Not found.")
    if row["visibility"] != "public" or row["hidden"]:
        u = _session_user(req)
        if not u or (u.id != row["owner_id"] and not u.is_admin):
            raise HTTPException(404, "Not found.")
    return FileResponse(DATA / "samples" / f"{vid}.mp3", media_type="audio/mpeg")


@app.get("/api/me/voices")
def my_voices(u: User = Depends(session_user)):
    return {"voices": [_voice_out(r, mine=True) for r in
                       q("SELECT * FROM voices WHERE owner_id=? ORDER BY created DESC", (u.id,))]}


async def _queue_clone(u: User, file: UploadFile, name: str, visibility: str, consent: str) -> dict:
    if consent.lower() not in ("yes", "true", "1", "on"):
        raise HTTPException(400, "Please confirm you have the speaker's permission (consent=yes).")
    if visibility not in ("public", "private"):
        raise HTTPException(400, "visibility must be 'public' or 'private'.")
    name = name.strip()[:60] or "My voice"
    if usage(u.id)["clones_used"] >= CLONES_PER_ACCOUNT:
        raise HTTPException(429, f"The free plan includes {CLONES_PER_ACCOUNT} voice clone per account. "
                                 "More clones come with Premium (coming soon).")
    if q1("SELECT COUNT(*) n FROM voices WHERE owner_id=? AND status!='failed'", (u.id,))["n"] >= MAX_USER_VOICES:
        raise HTTPException(409, f"You can keep up to {MAX_USER_VOICES} voices. Delete one first.")
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Audio file is over 10 MB.")
    vid = "u_" + uuid.uuid4().hex[:16]
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in"
        src.write_bytes(raw)
        out = DATA / "uploads" / f"{vid}.wav"
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-t", "30", "-ac", "1", "-ar", "24000",
                            "-af", "silenceremove=start_periods=1:start_threshold=-45dB", str(out)],
                           capture_output=True)
        if r.returncode != 0 or not out.exists():
            raise HTTPException(400, "We could not read that audio file. Try WAV, MP3, M4A or WebM.")
        secs = out.stat().st_size / (24000 * 2)
        if secs < 4:
            out.unlink(missing_ok=True)
            raise HTTPException(400, "The recording is too short. Use 10 to 20 seconds of speech.")
    ex("INSERT INTO voices(id, owner_id, name, visibility, created) VALUES(?,?,?,?,?)",
       (vid, u.id, name, visibility, now_iso()))
    charge(u.id, clones=1)
    _wake.set()
    pos = q1("SELECT COUNT(*) n FROM voices WHERE status IN ('queued','processing')")["n"]
    return {"voice": vid, "name": name, "status": "queued", "queue_position": pos,
            "message": "You're in the queue. We'll email you when your voice is ready."}


@app.post("/api/me/voices")
async def clone_web(file: UploadFile = File(...), name: str = Form(...), visibility: str = Form("private"),
                    consent: str = Form(""), u: User = Depends(session_user)):
    return await _queue_clone(u, file, name, visibility, consent)


class VoicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    visibility: str | None = Field(default=None, pattern="^(public|private)$")


@app.patch("/api/me/voices/{vid}")
def update_voice(vid: str, body: VoicePatch, u: User = Depends(session_user)):
    if not q1("SELECT 1 FROM voices WHERE id=? AND owner_id=?", (vid, u.id)):
        raise HTTPException(404, "Not found.")
    if body.name:
        ex("UPDATE voices SET name=? WHERE id=?", (body.name.strip()[:60], vid))
    if body.visibility:
        ex("UPDATE voices SET visibility=? WHERE id=?", (body.visibility, vid))
    return {"ok": True}


@app.delete("/api/me/voices/{vid}")
def delete_voice(vid: str, u: User = Depends(session_user)):
    if not q1("SELECT 1 FROM voices WHERE id=? AND owner_id=? AND status!='processing'", (vid, u.id)):
        raise HTTPException(404, "Not found, or still processing.")
    ex("DELETE FROM voices WHERE id=?", (vid,))
    for p in (DATA / "voices" / f"{vid}.safetensors", DATA / "samples" / f"{vid}.mp3", DATA / "uploads" / f"{vid}.wav"):
        p.unlink(missing_ok=True)
    _user_states.pop(vid, None)
    return {"ok": True}


# ---------------------------------------------------------------- public API (v1)
class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(default="aria", max_length=40)
    format: str = Field(default="mp3", pattern="^(mp3|wav)$")


@app.get("/v1/health")
def health():
    return {"ok": True, "voices": len(state.get("house", {})),
            "clone_queue": q1("SELECT COUNT(*) n FROM voices WHERE status IN ('queued','processing')")["n"],
            "music_queue": q1("SELECT COUNT(*) n FROM music WHERE status IN ('queued','processing')")["n"]}


@app.get("/v1/voices")
def v1_voices(u: User = Depends(api_user)):
    mine = [_voice_out(r, mine=True) for r in q("SELECT * FROM voices WHERE owner_id=? ORDER BY created DESC", (u.id,))]
    return {"voices": public_voices()["voices"], "mine": mine}


@app.post("/v1/tts")
async def v1_tts(body: TTSRequest, u: User = Depends(api_user)):
    text = body.text.strip()
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"Text is over {MAX_CHARS} characters. Split it into parts.")
    if usage(u.id)["chars"] + len(text) > DAILY_CHARS:
        raise HTTPException(429, f"You used today's {DAILY_CHARS} free characters. More tomorrow!")
    vs = voice_state_for(body.voice, u)
    audio = await asyncio.to_thread(synth, vs, text)
    charge(u.id, chars=len(text))
    data, mime = await asyncio.to_thread(encode, audio, state["model"].sample_rate, body.format)
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})


@app.post("/v1/voices/clone")
async def v1_clone(file: UploadFile = File(...), name: str = Form(...), visibility: str = Form("private"),
                   consent: str = Form(""), u: User = Depends(api_user)):
    return await _queue_clone(u, file, name, visibility, consent)


@app.get("/v1/voices/{vid}")
def v1_voice_status(vid: str, u: User = Depends(api_user)):
    row = q1("SELECT * FROM voices WHERE id=? AND owner_id=?", (vid, u.id))
    if not row:
        raise HTTPException(404, "Not found.")
    return _voice_out(row, mine=True)


@app.get("/v1/usage")
def v1_usage(u: User = Depends(api_user)):
    return {"day": today(), **usage_out(usage(u.id))}


# ---------------------------------------------------------------- music (queued, made by music/worker.py)
class MusicRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=300)
    duration: int = Field(default=30, ge=5, le=600)


def _music_out(r: sqlite3.Row) -> dict:
    d = {"id": r["id"], "prompt": r["prompt"], "duration": r["duration"], "status": r["status"],
         "error": r["error"], "created": r["created"], "finished": r["finished"],
         "audio": f"/v1/music/{r['id']}/audio" if r["status"] == "ready" else None}
    if r["status"] == "queued":
        d["queue_position"] = q1("SELECT COUNT(*) n FROM music WHERE status IN ('queued','processing') AND created<=?",
                                 (r["created"],))["n"]
    return d


def _queue_music(u: User, body: MusicRequest) -> dict:
    if body.duration > MUSIC_MAX_SEC:
        raise HTTPException(400, f"Free tracks can be up to {MUSIC_MAX_SEC} seconds. Longer tracks come with Premium.")
    if usage(u.id)["music"] >= DAILY_MUSIC:
        raise HTTPException(429, f"You can make {DAILY_MUSIC} free tracks a day. More tomorrow!")
    if q1("SELECT COUNT(*) n FROM music WHERE owner_id=? AND status IN ('queued','processing')", (u.id,))["n"] >= 2:
        raise HTTPException(429, "You already have 2 tracks waiting. Wait for one to finish.")
    mid = "m_" + uuid.uuid4().hex[:16]
    ex("INSERT INTO music(id, owner_id, prompt, duration, created) VALUES(?,?,?,?,?)",
       (mid, u.id, body.prompt.strip(), body.duration, now_iso()))
    charge(u.id, music=1)
    r = _music_out(q1("SELECT * FROM music WHERE id=?", (mid,)))
    r["message"] = "You're in the music queue. We'll email you when your track is ready."
    return r


@app.post("/api/me/music")
def music_web(body: MusicRequest, u: User = Depends(session_user)):
    return _queue_music(u, body)


@app.get("/api/me/music")
def my_music(u: User = Depends(session_user)):
    return {"tracks": [_music_out(r) for r in q("SELECT * FROM music WHERE owner_id=? ORDER BY created DESC LIMIT 50", (u.id,))]}


@app.delete("/api/me/music/{mid}")
def delete_music(mid: str, u: User = Depends(session_user)):
    if not q1("SELECT 1 FROM music WHERE id=? AND owner_id=? AND status!='processing'", (mid, u.id)):
        raise HTTPException(404, "Not found, or still being made.")
    ex("DELETE FROM music WHERE id=?", (mid,))
    (DATA / "music" / f"{mid}.mp3").unlink(missing_ok=True)
    return {"ok": True}


@app.post("/v1/music")
def v1_music(body: MusicRequest, u: User = Depends(api_user)):
    return _queue_music(u, body)


@app.get("/v1/music/{mid}")
def v1_music_status(mid: str, u: User = Depends(api_user)):
    row = q1("SELECT * FROM music WHERE id=? AND owner_id=?", (mid, u.id))
    if not row:
        raise HTTPException(404, "Not found.")
    return _music_out(row)


@app.get("/v1/music/{mid}/audio")
def v1_music_audio(mid: str, u: User = Depends(api_user)):
    row = q1("SELECT status FROM music WHERE id=? AND owner_id=?", (mid, u.id))
    path = DATA / "music" / f"{mid}.mp3"
    if not row or row["status"] != "ready" or not path.exists():
        raise HTTPException(404, "Not found, or not ready yet.")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{mid}.mp3")


# ---------------------------------------------------------------- admin
@app.get("/api/admin/stats")
def admin_stats(_: User = Depends(admin_user)):
    one = lambda s, a=(): q1(s, a)[0]  # noqa: E731
    return {
        "users": one("SELECT COUNT(*) FROM users"),
        "verified": one("SELECT COUNT(*) FROM users WHERE verified=1"),
        "keys": one("SELECT COUNT(*) FROM api_keys WHERE revoked=0"),
        "voices": {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM voices GROUP BY status")},
        "music": {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM music GROUP BY status")},
        "today": dict(q1("SELECT COALESCE(SUM(chars),0) chars, COALESCE(SUM(clones),0) clones, "
                         "COALESCE(SUM(music),0) music, "
                         "COALESCE(SUM(requests),0) requests FROM usage WHERE day=?", (today(),))),
        "last7": [dict(r) for r in q("SELECT day, SUM(chars) chars, SUM(requests) requests, SUM(clones) clones, "
                                     "SUM(music) music FROM usage GROUP BY day ORDER BY day DESC LIMIT 7")],
    }


@app.get("/api/admin/users")
def admin_users(_: User = Depends(admin_user)):
    rows = q("SELECT u.id, u.email, u.verified, u.disabled, u.created, u.last_login, "
             "(SELECT COALESCE(SUM(chars),0) FROM usage WHERE user_id=u.id) chars, "
             "(SELECT COUNT(*) FROM voices WHERE owner_id=u.id) voices "
             "FROM users u ORDER BY u.id DESC LIMIT 1000")
    return {"users": [dict(r) for r in rows]}


class AdminUserPatch(BaseModel):
    disabled: bool


@app.patch("/api/admin/users/{uid}")
def admin_user_patch(uid: int, body: AdminUserPatch, a: User = Depends(admin_user)):
    if uid == a.id:
        raise HTTPException(400, "You cannot turn off your own account.")
    ex("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), uid))
    if body.disabled:
        ex("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@app.get("/api/admin/voices")
def admin_voices(_: User = Depends(admin_user)):
    rows = q("SELECT v.id, v.name, v.visibility, v.status, v.hidden, v.error, v.created, v.finished, u.email "
             "FROM voices v JOIN users u ON u.id=v.owner_id ORDER BY v.created DESC LIMIT 500")
    return {"voices": [dict(r) for r in rows]}


class AdminVoicePatch(BaseModel):
    hidden: bool


@app.patch("/api/admin/voices/{vid}")
def admin_voice_patch(vid: str, body: AdminVoicePatch, _: User = Depends(admin_user)):
    ex("UPDATE voices SET hidden=? WHERE id=?", (int(body.hidden), vid))
    return {"ok": True}
