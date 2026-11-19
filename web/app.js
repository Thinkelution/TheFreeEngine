"use strict";
/* The Free Engine web app: no framework, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const S = { me: null, voices: [], peaks: {}, mine: [], unlocked: false, lib: { f: "all", q: "", grid: false } };
const ICON = {
  play: '<svg viewBox="0 0 24 24"><path class="i-play" d="M8 5v14l11-7z"/><path class="i-pause" d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  wave: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v3"/></svg>',
  code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/></svg>',
  gift: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 12h18M12 8S10 3 7.5 4 8 8 12 8zm0 0s2-5 4.5-4S16 8 12 8z"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
};

/* ------------------------------------------------------------ helpers */
async function api(method, path, body, form) {
  const opt = { method, credentials: "same-origin", headers: { "X-TFE": "1" } };
  if (form) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opt);
  const ct = r.headers.get("content-type") || "";
  if (!r.ok) {
    let msg = `Something went wrong (${r.status}).`;
    if (ct.includes("json")) {
      const j = await r.json();
      msg = j.error || (Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join(", ") : j.detail) || msg;
    }
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return ct.includes("json") ? r.json() : r.blob();
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 4200);
  setTimeout(() => t.remove(), 4600);
}
async function busy(btn, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>' + old;
  try { return await fn(); } catch (e) { toast(e.message, "bad"); } finally { btn.disabled = false; btn.innerHTML = old; }
}
const hue = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
function fakePeaks(id, n = 64) {
  let x = hue(id) + 7; const out = [];
  for (let i = 0; i < n; i++) { x = (x * 9301 + 49297) % 233280; out.push(0.25 + 0.6 * Math.abs(Math.sin(i / 4 + x / 233280 * 6))); }
  return out;
}
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const qs = () => new URLSearchParams(location.hash.split("?")[1] || "");

function drawWave(cv, peaks, prog = 0, h = 260, hover = -1) {
  const dpr = devicePixelRatio || 1, w = cv.clientWidth, ht = cv.clientHeight;
  if (!w) return;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = ht * dpr; }
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, ht);
  const bw = 3, gap = 2, n = Math.floor(w / (bw + gap));
  for (let i = 0; i < n; i++) {
    const p = peaks[Math.floor(i / n * peaks.length)] || 0.1;
    const bh = Math.max(3, p * ht * 0.92), x = i * (bw + gap), y = (ht - bh) / 2, f = i / n;
    g.fillStyle = f <= prog ? `hsl(${h + f * 50} 90% 68%)` : (hover >= 0 && f <= hover ? "rgba(255,255,255,.4)" : "rgba(255,255,255,.17)");
    g.beginPath(); g.roundRect ? g.roundRect(x, y, bw, bh, 1.5) : g.rect(x, y, bw, bh); g.fill();
  }
}
async function peaksFromBlob(blob, n = 120) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer()); ctx.close();
    const d = buf.getChannelData(0), step = Math.floor(d.length / n), out = [];
    for (let i = 0; i < n; i++) { let m = 0; for (let j = i * step; j < (i + 1) * step; j += 16) m = Math.max(m, Math.abs(d[j])); out.push(m); }
    const mx = Math.max(...out, 0.01); return out.map((v) => Math.pow(v / mx, 0.8));
  } catch { return fakePeaks(blob.size); }
}

/* ------------------------------------------------------------ player (bottom bar) */
const P = { el: $("#player"), audio: new Audio(), peaks: [], hue: 260, hover: -1 };
P.audio.preload = "auto";
function playTrack({ src, title, sub, peaks, h, download, blob }) {
  preview.stop();
  P.el.hidden = false; document.body.classList.add("has-player"); $("#p-title").textContent = title; $("#p-sub").textContent = sub || "";
  P.hue = h ?? 260; P.peaks = peaks || fakePeaks(title, 120);
  if (blob && !peaks) peaksFromBlob(blob).then((p) => { P.peaks = p; drawP(); });
  const dl = $("#p-dl"); dl.href = download || src; dl.setAttribute("download", (title || "speech").replace(/\W+/g, "-") + ".mp3");
  P.audio.src = src; P.audio.currentTime = 0;
  P.audio.play().catch(() => {}); drawP();
}
function drawP() { drawWave($("#p-wave"), P.peaks, P.audio.duration ? P.audio.currentTime / P.audio.duration : 0, P.hue, P.hover); }
P.audio.addEventListener("timeupdate", () => { $("#p-time").textContent = fmt(P.audio.currentTime); drawP(); });
P.audio.addEventListener("play", () => P.el.classList.add("on"));
P.audio.addEventListener("pause", () => P.el.classList.remove("on"));
P.audio.addEventListener("ended", () => { P.el.classList.remove("on"); drawP(); });
$("#p-play").onclick = () => (P.audio.paused ? P.audio.play() : P.audio.pause());
$("#p-close").onclick = () => { P.audio.pause(); P.audio.removeAttribute("src"); P.audio.load(); P.el.hidden = true; document.body.classList.remove("has-player"); };
const pw = $("#p-wave");
pw.addEventListener("click", (e) => { if (P.audio.duration) { P.audio.currentTime = (e.offsetX / pw.clientWidth) * P.audio.duration; P.audio.play(); } });
pw.addEventListener("mousemove", (e) => { P.hover = e.offsetX / pw.clientWidth; drawP(); });
pw.addEventListener("mouseleave", () => { P.hover = -1; drawP(); });
addEventListener("resize", drawP);
addEventListener("keydown", (e) => {
  if (e.code === "Space" && !P.el.hidden && !/INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName)) { e.preventDefault(); $("#p-play").click(); }
});

/* ------------------------------------------------------------ hover preview */
const preview = {
  audio: new Audio(), card: null, timer: 0, raf: 0,
  start(card) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      if (!S.unlocked) { $("#hover-hint")?.classList.add("flash"); return; }
      this.stop(); this.card = card; card.classList.add("playing");
      this.audio.src = card.dataset.src; this.audio.currentTime = 0;
      this.audio.play().catch(() => { card.classList.remove("playing"); });
      const tick = () => { if (this.card !== card) return; paintCard(card, this.audio.duration ? this.audio.currentTime / this.audio.duration : 0); this.raf = requestAnimationFrame(tick); };
      tick();
    }, 140);
  },
  stop() {
    clearTimeout(this.timer); cancelAnimationFrame(this.raf); this.audio.pause();
    if (this.card) { this.card.classList.remove("playing"); paintCard(this.card, 0); this.card = null; }
  },
};
preview.audio.preload = "none";
preview.audio.addEventListener("ended", () => preview.stop());
const unlock = () => { if (S.unlocked) return; S.unlocked = true; $("#hover-hint")?.remove(); };
addEventListener("pointerdown", unlock, { once: true, capture: true });
addEventListener("keydown", unlock, { once: true, capture: true });
const canHover = matchMedia("(hover: hover) and (pointer: fine)").matches;

function voiceById(id) { return S.voices.find((v) => v.id === id) || S.mine.find((v) => v.id === id); }
function paintCard(card, prog) { const v = voiceById(card.dataset.id); drawWave($("canvas", card), S.peaks[card.dataset.id] || fakePeaks(card.dataset.id), prog, hue(card.dataset.id)); }
function voiceCard(v) {
  const h = hue(v.id);
  const sub = v.type === "house" ? `${v.pitch === "high" ? "Higher" : "Lower"} voice · ${v.pace ? v.pace + " chars/s" : "natural pace"}` : "Community voice";
  const tags = v.type === "house"
    ? `<span class="tag a">★ ${esc(v.quality)}</span><span class="tag">${v.pitch === "high" ? "Bright" : "Deep"}</span>`
    : `<span class="tag ok">Community</span>`;
  return `<div class="vc" tabindex="0" role="button" aria-label="Play ${esc(v.name)}" data-id="${esc(v.id)}" data-src="${esc(v.sample)}" data-h="${h}">
    <div class="row"><div class="av">${esc(v.name[0] || "?")}</div><div class="meta"><b>${esc(v.name)}</b><small>${esc(sub)}</small></div>
    <span class="pl">${ICON.play}</span></div><canvas></canvas><div class="tags">${tags}</div>
    <button class="btn sm use" data-use="${esc(v.id)}">Use</button></div>`;
}
function wireCards(root) {
  for (const c of $$(".vc", root)) {
    c.style.setProperty("--h", c.dataset.h);
    paintCard(c, 0);
    if (canHover) { c.addEventListener("mouseenter", () => preview.start(c)); c.addEventListener("mouseleave", () => preview.stop()); }
    c.addEventListener("focus", () => canHover && S.unlocked && preview.start(c));
    c.addEventListener("blur", () => preview.stop());
    c.addEventListener("click", (e) => {
      const use = e.target.closest("[data-use]");
      if (use) { e.stopPropagation(); pickVoice(use.dataset.use); return; }
      const v = voiceById(c.dataset.id);
      playTrack({ src: c.dataset.src, title: v.name, sub: v.type === "house" ? "House voice sample" : "Community voice", peaks: S.peaks[v.id], h: +c.dataset.h });
    });
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") c.click(); });
  }
}
function pickVoice(id) {
  sessionStorage.setItem("tfe_voice", id);
  const sel = $("#voice-sel");
  if (sel) { sel.value = id; sel.dispatchEvent(new Event("change")); $("#tts-text")?.focus(); toast(`Voice set to ${voiceById(id)?.name || id}`); window.scrollTo({ top: 0 }); }
  else location.hash = "#/studio";
}

/* ------------------------------------------------------------ data */
async function loadMe() { try { S.me = await api("GET", "/api/me"); } catch { S.me = null; } renderWho(); }
async function loadVoices() {
  const [v, p] = await Promise.all([api("GET", "/api/voices").catch(() => ({ voices: [] })), fetch("/peaks.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}))]);
  S.voices = v.voices; S.peaks = p;
}
async function loadMine() { if (!S.me) return (S.mine = []); S.mine = (await api("GET", "/api/me/voices")).voices; }
function renderWho() {
  const w = $("#who");
  $("#nav-admin").classList.toggle("hide", !S.me?.admin);
  w.innerHTML = S.me
    ? `<span class="small muted hide-sm">${esc(S.me.email)}</span><button class="btn sm" id="logout">Log out</button>`
    : `<a class="btn sm" href="#/login">Log in</a><a class="btn sm pri" href="#/signup">Sign up free</a>`;
  $("#logout")?.addEventListener("click", async () => { await api("POST", "/auth/logout").catch(() => {}); S.me = null; renderWho(); location.hash = "#/"; toast("Logged out."); });
}
function voiceOptions(sel) {
  const g = (label, list) => (list.length ? `<optgroup label="${label}">${list.map((v) => `<option value="${esc(v.id)}"${v.id === sel ? " selected" : ""}>${esc(v.name)}</option>`).join("")}</optgroup>` : "");
  const mine = S.mine.filter((v) => v.status === "ready");
  return g("My voices", mine) + g("Higher voices", S.voices.filter((v) => v.type === "house" && v.pitch === "high")) +
    g("Lower voices", S.voices.filter((v) => v.type === "house" && v.pitch === "low")) +
    g("Community", S.voices.filter((v) => v.type === "community" && !mine.some((m) => m.id === v.id)));
}

/* ------------------------------------------------------------ generate */
async function generate(btn) {
  const text = $("#tts-text").value.trim(), voice = $("#voice-sel").value;
  if (!text) return toast("Type something first.", "bad");
  if (!S.me) { sessionStorage.setItem("tfe_draft", text); location.hash = "#/signup"; return toast("Make a free account to generate speech."); }
  await busy(btn, async () => {
    const t0 = performance.now();
    const blob = await api("POST", "/v1/tts", { text, voice, format: "mp3" });
    const url = URL.createObjectURL(blob), v = voiceById(voice);
    playTrack({ src: url, title: v?.name || voice, sub: text.slice(0, 60), blob, h: hue(voice), download: url });
    addHistory({ url, blob, voice: v?.name || voice, text, secs: ((performance.now() - t0) / 1000).toFixed(1) });
    S.me && api("GET", "/api/me").then((m) => { S.me = m; $("#usage") && renderUsage(); }).catch(() => {});
  });
}
const clips = [];
function addHistory(item) {
  clips.unshift(item); if (clips.length > 12) clips.pop();
  const el = $("#history"); if (!el) return;
  el.innerHTML = clips.map((h, i) => `<div class="li"><button class="btn sm" data-h="${i}" aria-label="Play">▶</button>
    <div class="grow"><b>${esc(h.voice)}</b> <span class="small muted">· made in ${h.secs}s</span><div class="small muted">${esc(h.text.slice(0, 90))}</div></div>
    <a class="btn sm" href="${h.url}" download="speech-${i + 1}.mp3">Download</a></div>`).join("");
  for (const b of $$("[data-h]", el)) b.onclick = () => { const h = clips[+b.dataset.h]; playTrack({ src: h.url, title: h.voice, sub: h.text.slice(0, 60), blob: h.blob, h: hue(h.voice), download: h.url }); };
}
function wireComposer() {
  const ta = $("#tts-text"), cnt = $("#tts-count"), max = S.me?.usage.max_chars_per_request || 1000;
  const draft = sessionStorage.getItem("tfe_draft"); if (draft && S.me) { ta.value = draft; sessionStorage.removeItem("tfe_draft"); }
  const upd = () => { cnt.textContent = `${ta.value.length} / ${max}`; cnt.style.color = ta.value.length > max ? "var(--bad)" : ""; };
  ta.addEventListener("input", upd); upd();
  ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") $("#gen").click(); });
  $("#gen").onclick = (e) => generate(e.currentTarget);
  const sel = $("#voice-sel"), saved = sessionStorage.getItem("tfe_voice");
  if (saved && voiceById(saved)) sel.value = saved;
  sel.addEventListener("change", () => sessionStorage.setItem("tfe_voice", sel.value));
}

/* ------------------------------------------------------------ pages */
function libraryHTML() {
  return `<section class="wrap" id="voices">
    <div class="libhead"><div><h2>Pick a voice</h2><p class="muted" style="margin:0">${S.voices.length} voices, all free.
      ${canHover ? '<span class="hint" id="hover-hint">· Click once anywhere, then hover a card to hear it</span>' : "Tap a card to hear it."}</p></div>
      <div class="filters">
        ${["all", "high", "low", "community"].map((f) => `<button class="chip${S.lib.f === f ? " on" : ""}" data-f="${f}">${{ all: "All", high: "Higher", low: "Lower", community: "Community" }[f]}</button>`).join("")}
        <label class="search">${ICON.search}<input class="in" id="lib-q" placeholder="Search voices" value="${esc(S.lib.q)}" aria-label="Search voices"></label>
        <button class="btn sm" id="lib-grid">${S.lib.grid ? "Scroll view" : "Show all"}</button>
        <span class="railnav${S.lib.grid ? " hide" : ""}"><button class="btn sm" id="rl" aria-label="Scroll left">←</button><button class="btn sm" id="rr" aria-label="Scroll right">→</button></span>
      </div></div>
    <div class="rail${S.lib.grid ? " grid" : ""}" id="rail"></div></section>`;
}
function renderRail() {
  const rail = $("#rail"); if (!rail) return;
  const q = S.lib.q.toLowerCase();
  const list = S.voices.filter((v) => (S.lib.f === "all" || (S.lib.f === "community" ? v.type === "community" : v.pitch === S.lib.f)) && (!q || v.name.toLowerCase().includes(q)));
  rail.innerHTML = list.length ? list.map(voiceCard).join("") : `<div class="empty">${S.lib.f === "community" ? "No public community voices yet. Clone yours and share it!" : "No voices match."}</div>`;
  wireCards(rail);
}
function wireLibrary() {
  for (const c of $$("[data-f]")) c.onclick = () => { S.lib.f = c.dataset.f; $$("[data-f]").forEach((x) => x.classList.toggle("on", x === c)); renderRail(); };
  $("#lib-q").oninput = (e) => { S.lib.q = e.target.value; renderRail(); };
  $("#lib-grid").onclick = (e) => { S.lib.grid = !S.lib.grid; $("#rail").classList.toggle("grid", S.lib.grid); $(".railnav").classList.toggle("hide", S.lib.grid); e.target.textContent = S.lib.grid ? "Scroll view" : "Show all"; renderRail(); };
  $("#rl").onclick = () => $("#rail").scrollBy({ left: -600, behavior: "smooth" });
  $("#rr").onclick = () => $("#rail").scrollBy({ left: 600, behavior: "smooth" });
  renderRail();
}
function composerHTML(big) {
  return `<div class="${big ? "try" : "card"}"><textarea class="in" id="tts-text" maxlength="5000" placeholder="Type anything and hear it in any voice…">${big ? "Welcome to The Free Engine. Pick any voice and make it say anything, for free." : ""}</textarea>
    <div class="bar"><label class="chipsel">${ICON.wave}<select id="voice-sel" aria-label="Voice">${voiceOptions("aria")}</select></label>
    <span class="small muted grow" id="tts-count"></span><button class="btn pri" id="gen">${S.me ? "Generate" : "Generate free"} <kbd class="small" style="opacity:.6">⌘↵</kbd></button></div></div>`;
}

const pages = {
  async home() {
    view.innerHTML = `<div class="fade">
      <section class="wrap hero"><span class="tag a"><span class="dot pulse"></span> Free forever · open models</span>
        <h1 style="margin-top:18px">Every voice.<br><span class="grad">Totally free.</span></h1>
        <p class="lead">${S.voices.length || 60} natural voices, free voice cloning, and a simple API. No card, no catch. Just sign up and start talking.</p>
        <div class="ctas">${S.me ? '<a class="btn pri" href="#/studio">Open studio</a>' : '<a class="btn pri" href="#/signup">Get your free API key</a>'}<a class="btn" href="#voices">Hear the voices</a></div>
        ${composerHTML(true)}
        <div class="stats"><div><b>${S.voices.filter((v) => v.type === "house").length || 60}</b><span>house voices</span></div><div><b>5K</b><span>free chars / day</span></div><div><b>Free</b><span>voice cloning</span></div><div><b>♪</b><span>free music</span></div></div>
      </section>
      ${libraryHTML()}
      <section class="wrap"><div class="feat">
        <div class="card"><div class="ic">${ICON.gift}</div><h3>Free, really</h3><p>5,000 characters a day, 1 free voice clone, 3 music tracks a day. No credit card.</p></div>
        <div class="card"><div class="ic">${ICON.code}</div><h3>Simple API</h3><p>One POST, get an MP3 back. Works from any language.</p></div>
        <div class="card"><div class="ic">${ICON.lock}</div><h3>Your voice, your rules</h3><p>Keep a cloned voice private to your account, or share it with everyone.</p></div>
        <div class="card"><div class="ic">${ICON.bolt}</div><h3>Open models</h3><p>Built on Kyutai Pocket TTS. Every house voice lists its public source.</p></div>
      </div></section>
      <section class="wrap"><div class="card callout"><div>
        <span class="tag ok">Free</span><h2 style="margin-top:12px">Clone your own voice, free</h2>
        <p class="muted">Upload or record 10 to 20 seconds of clear speech. Cloning is free for everyone, so requests wait in a fair queue.
        It can take a while. We'll email you the moment your voice is ready.</p>
        <a class="btn pri" href="#/clone">Clone a voice</a></div>
        <ol><li><div><b>Record or upload</b><div class="muted small">10–20 s of one person talking, no music.</div></div></li>
          <li><div><b>Join the queue</b><div class="muted small">Free clones are made in order. You can see your place.</div></div></li>
          <li><div><b>Get an email</b><div class="muted small">We email you when it's ready to use.</div></div></li>
          <li><div><b>Private or public</b><div class="muted small">Private voices only work in your account. Public ones join the library.</div></div></li></ol></div></section>
      <section class="wrap"><div class="cols"><div><h2>Three lines to speech</h2><p class="muted">Get a key in the studio, then:</p>
<pre><code>curl ${location.origin}/v1/tts \\
  -H "Authorization: Bearer $TFE_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"text":"Hello world!","voice":"aria"}' -o hello.mp3</code></pre><a class="btn" href="#/docs">Read the API docs</a></div>
        <div class="card"><h3>Plans</h3><table class="tbl"><tr><th></th><th>Free</th><th>Premium</th></tr>
          <tr><td>Characters per day</td><td><b>5,000</b></td><td>More</td></tr><tr><td>Characters per request</td><td><b>1,000</b></td><td>More</td></tr>
          <tr><td>Voice clones</td><td><b>1 per account</b></td><td>More</td></tr><tr><td>Music tracks</td><td><b>3 a day, 30 s</b></td><td>Longer, more</td></tr>
          <tr><td>Queue</td><td>Shared</td><td>Priority</td></tr><tr><td>Price</td><td><b class="grad">$0</b></td><td><span class="tag warn">Coming soon</span></td></tr></table></div></div></section>
    </div>`;
    wireComposer(); wireLibrary();
  },

  async studio() {
    if (!S.me) return need("studio");
    await loadMine();
    view.innerHTML = `<div class="wrap page fade"><div class="page-h"><div><h1>Studio</h1><p class="muted" style="margin:0">Type, pick a voice, listen.</p></div>
      <a class="btn" href="#/clone">+ Clone a voice</a></div>
      <div class="cols"><div>${composerHTML(false)}<h3 style="margin:26px 0 12px">This session</h3><div class="list" id="history"><div class="empty small">Nothing yet. Your clips show up here.</div></div></div>
      <div class="list"><div class="card" id="usage"></div><div class="card" id="keys"></div></div></div></div>
      ${libraryHTML()}`;
    wireComposer(); renderUsage(); renderKeys(); wireLibrary(); if (clips.length) addHistory(clips.shift());
  },

  async clone() {
    if (S.me) await loadMine();
    view.innerHTML = `<div class="wrap page fade"><div class="page-h"><div><h1>Clone a voice <span class="tag ok" style="vertical-align:middle">Free</span></h1>
      <p class="muted" style="margin:0">Every account gets 1 free clone. It waits in a queue, and we email you when it's ready. More clones come with Premium (coming soon).</p></div></div>
      <div class="cols"><div class="card">${S.me ? cloneForm() : `<p>Make a free account to clone a voice.</p><a class="btn pri" href="#/signup">Sign up free</a> <a class="btn" href="#/login">Log in</a>`}</div>
      <div><div class="notice info" style="margin-bottom:14px"><b>How the queue works.</b> Cloning is free, so every request waits its turn.
        It can take some time when many people are cloning. You'll get an email at <b>${esc(S.me?.email || "your address")}</b> when your voice is ready.</div>
        <h3>My voices</h3><div class="list" id="mine">${S.me ? "" : '<div class="empty small">Log in to see your voices.</div>'}</div></div></div></div>`;
    if (S.me) { wireCloneForm(); renderMine(); }
  },

  async docs() {
    const o = location.origin;
    const rows = [["GET", "/v1/voices", "All voices you can use"], ["POST", "/v1/tts", "Text to speech (MP3 or WAV)"], ["POST", "/v1/voices/clone", "Queue a voice clone"],
      ["GET", "/v1/voices/{id}", "Check a clone's status"], ["POST", "/v1/music", "Queue a music track"], ["GET", "/v1/music/{id}", "Check a track's status"],
      ["GET", "/v1/music/{id}/audio", "Download a finished track (MP3)"], ["GET", "/v1/usage", "Today's use and limits"]];
    view.innerHTML = `<div class="wrap page fade" style="max-width:900px"><h1>API</h1><p class="muted">Base URL <code>${o}</code>. Send your key as <code>Authorization: Bearer tfe_…</code>. Get a key in the <a href="#/studio">studio</a>.</p>
      <div class="card tblwrap" style="margin:18px 0 30px"><table class="tbl"><tr><th>Method</th><th>Path</th><th>What it does</th></tr>
      ${rows.map(([m, p, d]) => `<tr><td><span class="m ${m.toLowerCase()}">${m}</span></td><td><code>${p}</code></td><td>${d}</td></tr>`).join("")}</table></div>
      <h3>Text to speech</h3><p class="muted">Body: <code>text</code> (up to 1,000 chars), <code>voice</code> (an id from <code>/v1/voices</code>), <code>format</code> (<code>mp3</code> or <code>wav</code>).</p>
<pre><code>curl ${o}/v1/tts -H "Authorization: Bearer $TFE_KEY" -H "Content-Type: application/json" \\
  -d '{"text":"Hello from The Free Engine!","voice":"aria","format":"mp3"}' -o hello.mp3</code></pre>
<pre><code>import requests
r = requests.post("${o}/v1/tts", headers={"Authorization": f"Bearer {KEY}"},
                  json={"text": "Hello!", "voice": "leo"})
open("hello.mp3", "wb").write(r.content)</code></pre>
<pre><code>const r = await fetch("${o}/v1/tts", { method: "POST",
  headers: { Authorization: \`Bearer \${KEY}\`, "Content-Type": "application/json" },
  body: JSON.stringify({ text: "Hello!", voice: "maya" }) });
const mp3 = await r.blob();</code></pre>
      <h3>Clone a voice</h3><p class="muted">Free. Send 10 to 20 seconds of clean speech. It goes into the queue and we email you when it's ready.
      <code>visibility</code> is <code>private</code> (only your account) or <code>public</code> (shown in the library). <code>consent=yes</code> confirms you have the speaker's permission.</p>
<pre><code>curl ${o}/v1/voices/clone -H "Authorization: Bearer $TFE_KEY" \\
  -F file=@my_voice.wav -F name="My voice" -F visibility=private -F consent=yes
# → {"voice":"u_…","status":"queued","queue_position":3}

curl ${o}/v1/voices/u_… -H "Authorization: Bearer $TFE_KEY"   # status: queued → processing → ready</code></pre>
      <p class="muted small">Free plan: 1 voice clone per account. More with Premium (coming soon).</p>
      <h3>Make music</h3><p class="muted">Free: 3 instrumental tracks a day, up to 30 seconds. Tracks are queued; poll the status or wait for our email.</p>
<pre><code>curl ${o}/v1/music -H "Authorization: Bearer $TFE_KEY" -H "Content-Type: application/json" \\
  -d '{"prompt":"Calm lofi beat with soft piano 80 BPM","duration":30}'
# → {"id":"m_…","status":"queued","queue_position":2}

curl ${o}/v1/music/m_… -H "Authorization: Bearer $TFE_KEY"          # queued → processing → ready
curl ${o}/v1/music/m_…/audio -H "Authorization: Bearer $TFE_KEY" -o track.mp3</code></pre>
      <p class="muted small">Music powered by Stability AI.</p>
      <h3>Errors</h3><div class="card tblwrap"><table class="tbl"><tr><th>Code</th><th>Meaning</th></tr><tr><td>401</td><td>Missing or wrong key</td></tr><tr><td>404</td><td>Unknown voice</td></tr>
      <tr><td>413</td><td>Text over 1,000 chars, or file over 10 MB</td></tr><tr><td>429</td><td>Daily limit reached</td></tr></table></div>
      <p class="muted small" style="margin-top:18px">Errors come back as <code>{"error": "…"}</code>. Full schema: <a href="/v1/docs">/v1/docs</a>.</p>
      <h3 style="margin-top:30px">Fair use</h3><p class="muted">Only clone a voice with the speaker's clear permission. Never use a voice to trick, scam or harm people. Accounts that do are shut off.</p></div>`;
  },

  login() { authPage("Welcome back", "Log in to your studio.", ["email", "password"], "Log in", async (d) => { await api("POST", "/auth/login", d); await afterLogin(); },
    `<a href="#/forgot">Forgot password?</a> · No account? <a href="#/signup">Sign up free</a>`); },
  signup() { authPage("Make a free account", "Get your API key in a minute. We'll send a link to confirm your email.", ["email", "newpassword"], "Sign up free",
    async (d) => { await api("POST", "/auth/signup", d); sessionStorage.setItem("tfe_email", d.email); location.hash = "#/check-email"; }, `Have an account? <a href="#/login">Log in</a>`); },
  forgot() { authPage("Reset password", "We'll email you a link.", ["email"], "Send link", async (d) => { const r = await api("POST", "/auth/forgot", d); toast(r.message, "ok"); }, `<a href="#/login">Back to log in</a>`); },
  reset() { authPage("Pick a new password", "", ["newpassword"], "Save password", async (d) => { await api("POST", "/auth/reset", { token: qs().get("token"), password: d.password }); await afterLogin("Password changed."); }); },
  "check-email"() {
    const email = sessionStorage.getItem("tfe_email") || "";
    view.innerHTML = `<div class="auth card fade" style="text-align:center"><div class="ic" style="margin:0 auto 14px">✉</div><h1>Check your inbox</h1>
      <p class="muted">We sent a confirmation link to <b>${esc(email)}</b>. Click it to finish signing up.</p><p class="small muted">Nothing there? Check spam, or</p>
      <button class="btn" id="resend">Send it again</button></div>`;
    $("#resend").onclick = (e) => busy(e.currentTarget, async () => { await api("POST", "/auth/resend", { email }); toast("Sent again.", "ok"); });
  },
  async verify() {
    view.innerHTML = `<div class="auth card fade" style="text-align:center"><h1>Confirming…</h1><p class="muted">One moment.</p></div>`;
    try { await api("POST", "/auth/verify", { token: qs().get("token") }); await afterLogin("Email confirmed. Welcome!"); }
    catch (e) { view.innerHTML = `<div class="auth card fade" style="text-align:center"><h1>Link not valid</h1><p class="muted">${esc(e.message)}</p><a class="btn pri" href="#/login">Log in</a></div>`; }
  },

  async admin() {
    if (!S.me?.admin) return pages.home();
    const [st, us, vs] = await Promise.all([api("GET", "/api/admin/stats"), api("GET", "/api/admin/users"), api("GET", "/api/admin/voices")]);
    const vstat = Object.entries(st.voices).map(([k, n]) => `${k}: ${n}`).join(" · ") || "none";
    view.innerHTML = `<div class="wrap page fade"><div class="page-h"><h1>Admin</h1><span class="tag a">Only ${esc(S.me.email)}</span></div>
      <div class="kpis"><div class="card"><b>${st.users}</b><span>accounts (${st.verified} confirmed)</span></div><div class="card"><b>${st.keys}</b><span>active keys</span></div>
        <div class="card"><b>${st.today.chars.toLocaleString()}</b><span>chars today</span></div><div class="card"><b>${st.today.requests}</b><span>requests today</span></div>
        <div class="card"><b>${st.today.clones}</b><span>clones today</span></div><div class="card"><b>${st.today.music}</b><span>tracks today</span></div></div>
      <p class="muted small">Cloned voices: ${esc(vstat)} · Music: ${esc(Object.entries(st.music || {}).map(([k, n]) => `${k}: ${n}`).join(" · ") || "none")}</p>
      <div class="tabs"><button class="on" data-t="u">Users</button><button data-t="v">Cloned voices</button><button data-t="d">Last 7 days</button></div>
      <div class="card tblwrap" id="adm"></div></div>`;
    const tabs = {
      u: () => `<table class="tbl"><tr><th>Email</th><th>Status</th><th>Chars (all time)</th><th>Voices</th><th>Joined</th><th>Last login</th><th></th></tr>
        ${us.users.map((u) => `<tr><td>${esc(u.email)}</td><td>${u.disabled ? '<span class="tag bad">off</span>' : u.verified ? '<span class="tag ok">active</span>' : '<span class="tag warn">unconfirmed</span>'}</td>
        <td>${u.chars.toLocaleString()}</td><td>${u.voices}</td><td class="small">${esc(u.created.slice(0, 10))}</td><td class="small">${esc((u.last_login || "").slice(0, 16).replace("T", " "))}</td>
        <td><button class="btn sm ${u.disabled ? "" : "danger"}" data-u="${u.id}" data-d="${u.disabled ? 0 : 1}">${u.disabled ? "Turn on" : "Turn off"}</button></td></tr>`).join("")}</table>`,
      v: () => `<table class="tbl"><tr><th>Name</th><th>Owner</th><th>Status</th><th>Visibility</th><th>Created</th><th></th></tr>
        ${vs.voices.map((v) => `<tr><td>${esc(v.name)} <span class="small muted">${esc(v.id)}</span></td><td>${esc(v.email)}</td><td>${statusTag(v)}</td><td>${esc(v.visibility)}${v.hidden ? ' <span class="tag bad">hidden</span>' : ""}</td>
        <td class="small">${esc(v.created.slice(0, 16).replace("T", " "))}</td><td>${v.status === "ready" ? `<button class="btn sm" data-play="${esc(v.id)}">▶</button> <button class="btn sm" data-v="${esc(v.id)}" data-hd="${v.hidden ? 0 : 1}">${v.hidden ? "Unhide" : "Hide"}</button>` : ""}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No cloned voices yet.</td></tr>'}</table>`,
      d: () => `<table class="tbl"><tr><th>Day</th><th>Requests</th><th>Chars</th><th>Clones</th><th>Tracks</th></tr>${st.last7.map((d) => `<tr><td>${d.day}</td><td>${d.requests}</td><td>${(d.chars || 0).toLocaleString()}</td><td>${d.clones}</td><td>${d.music || 0}</td></tr>`).join("")}</table>`,
    };
    const show = (t) => {
      $("#adm").innerHTML = tabs[t]();
      for (const b of $$("[data-u]")) b.onclick = () => busy(b, async () => { await api("PATCH", `/api/admin/users/${b.dataset.u}`, { disabled: b.dataset.d === "1" }); pages.admin(); });
      for (const b of $$("[data-v]")) b.onclick = () => busy(b, async () => { await api("PATCH", `/api/admin/voices/${b.dataset.v}`, { hidden: b.dataset.hd === "1" }); pages.admin(); });
      for (const b of $$("[data-play]")) b.onclick = () => playTrack({ src: `/api/voices/${b.dataset.play}/sample`, title: b.dataset.play, sub: "Cloned voice" });
    };
    for (const b of $$("[data-t]")) b.onclick = () => { $$("[data-t]").forEach((x) => x.classList.toggle("on", x === b)); show(b.dataset.t); };
    show("u");
  },
};

/* ------------------------------------------------------------ music */
const MUSIC_IDEAS = [
  ["Lofi", "Calm lofi hip hop beat with soft piano, warm vinyl crackle and mellow drums 80 BPM"],
  ["House", "Upbeat house track with punchy kick, bright synth chords and a big build 124 BPM"],
  ["Cinematic", "Slow cinematic score with soaring strings, deep brass and gentle piano"],
  ["Ambient", "Dreamy ambient pad with soft textures and distant bells, relaxing"],
  ["Rock", "Energetic indie rock instrumental with driving drums and crunchy guitars 140 BPM"],
  ["Jazz", "Smooth jazz trio with upright bass, brushed drums and warm piano 95 BPM"],
  ["Indian", "Peaceful Indian classical instrumental with sitar and tabla, evening raga mood"],
  ["Game", "Cheerful 8-bit chiptune video game music, bouncy and fun 150 BPM"],
];
let musicTimer = 0;
pages.music = async function () {
  const u = S.me?.usage;
  view.innerHTML = `<div class="wrap page fade"><div class="page-h"><div><h1>Make music <span class="tag ok" style="vertical-align:middle">Free</span></h1>
    <p class="muted" style="margin:0">Describe a sound and get an instrumental track. Free tracks wait in a queue, and we email you when yours is ready.</p></div></div>
    <div class="cols"><div class="card">${S.me ? `<form id="mf"><div class="field"><label for="m-prompt">Describe your music</label>
      <textarea class="in" id="m-prompt" name="prompt" maxlength="300" minlength="3" required placeholder="e.g. Calm lofi beat with soft piano and rain, 80 BPM"></textarea></div>
      <div class="filters" style="margin:-4px 0 16px">${MUSIC_IDEAS.map(([k], i) => `<button type="button" class="chip" data-idea="${i}">${k}</button>`).join("")}</div>
      <div class="field"><label>Length</label><div class="seg">${[10, 20, 30].map((s) => `<label><input type="radio" name="duration" value="${s}"${s === 30 ? " checked" : ""}>${s} s</label>`).join("")}
        <label class="muted" title="Coming soon with Premium" style="cursor:not-allowed;opacity:.55">60 s+ ⭐</label></div></div>
      <button class="btn pri" type="submit" style="width:100%">Join the free music queue</button>
      <p class="small muted" style="text-align:center;margin:10px 0 0">${u.music_limit - u.music} of ${u.music_limit} free tracks left today · instrumental only</p></form>`
      : `<p>Make a free account to create music.</p><a class="btn pri" href="#/signup">Sign up free</a> <a class="btn" href="#/login">Log in</a>`}</div>
    <div><div class="notice info" style="margin-bottom:14px"><b>How it works.</b> Music is made on our own free servers, one track at a time.
      A track can take several minutes, plus your wait in line. We'll email you at <b>${esc(S.me?.email || "your address")}</b> when it's done.
      ⭐ Premium (coming soon) adds longer tracks and a priority queue.</div>
      <h3>My tracks</h3><div class="list" id="tracks">${S.me ? "" : '<div class="empty small">Log in to see your tracks.</div>'}</div>
      <p class="small muted" style="margin-top:14px">Music powered by <a href="https://stability.ai/" rel="noopener">Stability AI</a> (Stable Audio 3 Small, Stability AI Community License).</p></div></div></div>`;
  if (!S.me) return;
  for (const b of $$("[data-idea]")) b.onclick = () => { $("#m-prompt").value = MUSIC_IDEAS[+b.dataset.idea][1]; $("#m-prompt").focus(); };
  $("#mf").onsubmit = (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    busy($("#mf button[type=submit]"), async () => {
      const r = await api("POST", "/api/me/music", { prompt: d.prompt, duration: +d.duration });
      toast(`You're #${r.queue_position} in the music queue. We'll email you when it's ready.`, "ok");
      S.me = await api("GET", "/api/me"); pages.music();
    });
  };
  renderTracks();
};
async function renderTracks() {
  const el = $("#tracks"); if (!el) return;
  const { tracks } = await api("GET", "/api/me/music");
  el.innerHTML = tracks.length ? tracks.map((t) => `<div class="li"><div class="av" data-hue="${hue(t.id)}">♪</div>
    <div class="grow"><b>${esc(t.prompt.slice(0, 70))}</b> ${statusTag(t, "Making now")}<div class="small muted">${t.duration} s · ${esc(t.created.slice(0, 16).replace("T", " "))}${t.error ? " · failed, not counted" : ""}</div></div>
    ${t.status === "ready" ? `<button class="btn sm" data-tp="${esc(t.id)}">▶</button><a class="btn sm" href="${t.audio}" download="${esc(t.id)}.mp3">Download</a>` : ""}
    ${t.status !== "processing" ? `<button class="btn sm danger" data-td="${esc(t.id)}" aria-label="Delete">✕</button>` : ""}</div>`).join("")
    : '<div class="empty small">No tracks yet. Your music shows up here.</div>';
  for (const a of $$("[data-hue]", el)) a.style.setProperty("--h", a.dataset.hue);
  for (const b of $$("[data-tp]", el)) b.onclick = () => { const t = tracks.find((x) => x.id === b.dataset.tp); playTrack({ src: t.audio, title: "My track", sub: t.prompt.slice(0, 60), h: hue(t.id), download: t.audio }); };
  for (const b of $$("[data-td]", el)) b.onclick = () => { if (confirm("Delete this track?")) busy(b, async () => { await api("DELETE", `/api/me/music/${b.dataset.td}`); renderTracks(); }); };
  clearTimeout(musicTimer);
  if (tracks.some((t) => t.status === "queued" || t.status === "processing")) {
    const ready = tracks.filter((t) => t.status === "ready").length;
    musicTimer = setTimeout(async () => { if (!$("#tracks")) return; await renderTracks(); if ($$("#tracks [data-tp]").length > ready) toast("Your track is ready! 🎵", "ok"); }, 15000);
  }
}

function need(where) {
  sessionStorage.setItem("tfe_next", "#/" + where);
  view.innerHTML = `<div class="auth card fade" style="text-align:center"><h1>Log in first</h1><p class="muted">The ${where} is free. You just need an account.</p>
    <a class="btn pri" href="#/signup">Sign up free</a> <a class="btn" href="#/login">Log in</a></div>`;
}
async function afterLogin(msg = "You're in!") {
  await loadMe(); toast(msg, "ok");
  const next = sessionStorage.getItem("tfe_next"); sessionStorage.removeItem("tfe_next");
  location.hash = next || "#/studio";
}
function authPage(title, sub, fields, cta, fn, alt = "") {
  const F = {
    email: `<div class="field"><label for="f-email">Email</label><input class="in" id="f-email" name="email" type="email" autocomplete="email" required></div>`,
    password: `<div class="field"><label for="f-pw">Password</label><input class="in" id="f-pw" name="password" type="password" autocomplete="current-password" required></div>`,
    newpassword: `<div class="field"><label for="f-pw">Password <span class="muted">(8+ characters)</span></label><input class="in" id="f-pw" name="password" type="password" minlength="8" autocomplete="new-password" required></div>`,
  };
  view.innerHTML = `<form class="auth card fade" id="af"><h1>${title}</h1>${sub ? `<p class="muted">${sub}</p>` : ""}${fields.map((f) => F[f]).join("")}
    <button class="btn pri" style="width:100%" type="submit">${cta}</button>${alt ? `<div class="alt">${alt}</div>` : ""}</form>`;
  $("#af").onsubmit = (e) => { e.preventDefault(); busy($("#af button[type=submit]"), () => fn(Object.fromEntries(new FormData(e.target)))); };
  $("#af input")?.focus();
}

function renderUsage() {
  const u = S.me.usage, pc = (a, b) => Math.min(100, (a / b) * 100).toFixed(1) + "%";
  $("#usage").innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">Your plan</h3><span class="tag ok">Free</span></div>
    <div class="small muted" style="margin-top:12px">Characters today · ${u.chars.toLocaleString()} of ${u.chars_limit.toLocaleString()}</div><div class="meter"><i></i></div>
    <div class="small muted" style="margin-top:12px">Music today · ${u.music} of ${u.music_limit}</div><div class="meter"><i></i></div>
    <div class="small muted" style="margin-top:12px">Voice clone · ${u.clones_used} of ${u.clones_limit} (per account)</div><div class="meter"><i></i></div>
    <div class="small muted" style="margin-top:10px">Daily limits reset at midnight UTC.</div>
    <div class="notice" style="margin-top:14px">⭐ <b>Premium is coming soon:</b> more characters, more clones, longer music, priority queue.</div>`;
  const m = $$("#usage .meter i"); m[0].style.setProperty("--w", pc(u.chars, u.chars_limit)); m[1].style.setProperty("--w", pc(u.music, u.music_limit)); m[2].style.setProperty("--w", pc(u.clones_used, u.clones_limit));
}
async function renderKeys(fresh) {
  const el = $("#keys"); if (!el) return;
  const { keys } = await api("GET", "/api/keys");
  el.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">API keys</h3><button class="btn sm" id="newkey">+ New key</button></div>
    ${fresh ? `<div class="keybox"><code>${esc(fresh)}</code><button class="btn sm" id="copykey">Copy</button></div><div class="small" style="color:var(--warn)">Copy it now. You won't see it again.</div>` : ""}
    <div class="list" style="margin-top:12px">${keys.map((k) => `<div class="li"><div class="grow"><b>${esc(k.name)}</b><div class="small muted"><code>${esc(k.prefix)}…</code> · ${k.last_used ? "used " + esc(k.last_used.slice(0, 10)) : "never used"}</div></div>
    <button class="btn sm danger" data-k="${k.id}">Delete</button></div>`).join("") || '<div class="small muted">No keys yet. Make one to use the API.</div>'}</div>`;
  $("#newkey").onclick = (e) => busy(e.currentTarget, async () => { const name = prompt("Name this key", "My app"); if (name === null) return; const r = await api("POST", "/api/keys", { name }); renderKeys(r.key); });
  $("#copykey")?.addEventListener("click", () => navigator.clipboard.writeText(fresh).then(() => toast("Copied.", "ok")));
  for (const b of $$("[data-k]", el)) b.onclick = () => { if (confirm("Delete this key? Apps using it will stop working.")) busy(b, async () => { await api("DELETE", `/api/keys/${b.dataset.k}`); renderKeys(); }); };
}

/* ------------------------------------------------------------ cloning */
let recBlob = null;
function cloneForm() {
  return `<form id="cf"><div class="field"><label for="c-name">Voice name</label><input class="in" id="c-name" name="name" maxlength="60" placeholder="e.g. My narrator voice" required></div>
    <div class="field"><label>Recording</label><div class="drop" id="drop" tabindex="0"><b>Drop an audio file, or click to pick one</b><span class="small muted">WAV, MP3, M4A or WebM · 10–20 seconds · one speaker, no music</span>
      <input type="file" id="c-file" accept="audio/*" hidden></div>
      <div class="rec"><span class="small muted">or</span><button type="button" class="btn sm" id="rec">${ICON.mic.replace("<svg", '<svg width="16" height="16"')} Record now</button><span class="small muted" id="rec-st"></span></div>
      <div class="small" id="c-picked" style="margin-top:8px"></div></div>
    <div class="field"><label>Who can use it?</label><div class="seg"><label><input type="radio" name="visibility" value="private" checked>🔒 Private</label><label><input type="radio" name="visibility" value="public">🌍 Public</label></div>
      <span class="small muted" id="vis-help">Private: only your account can use this voice.</span></div>
    <label class="check" style="margin:6px 0 18px"><input type="checkbox" name="consent" value="yes" required> <span>This is my voice, or I have clear permission from the speaker to clone it.</span></label>
    <button class="btn pri" type="submit" style="width:100%">Join the free clone queue</button>
    <p class="small muted" style="text-align:center;margin:10px 0 0">${S.me.usage.clones_used >= S.me.usage.clones_limit ? "You've used your free clone. More clones come with Premium (coming soon)." : "1 free clone per account · we'll email you when it's ready"}</p></form>`;
}
function wireCloneForm() {
  const drop = $("#drop"), file = $("#c-file"), picked = $("#c-picked");
  const setFile = (f, label) => { recBlob = f; picked.innerHTML = f ? `✓ <b>${esc(label)}</b> <span class="muted">(${(f.size / 1024).toFixed(0)} KB)</span> <button type="button" class="btn sm" id="c-prev">Listen</button>` : "";
    $("#c-prev")?.addEventListener("click", () => playTrack({ src: URL.createObjectURL(f), title: "Your recording", sub: label, blob: f })); };
  drop.onclick = () => file.click(); drop.onkeydown = (e) => e.key === "Enter" && file.click();
  file.onchange = () => file.files[0] && setFile(file.files[0], file.files[0].name);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); }; drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) setFile(f, f.name); };
  for (const r of $$('input[name="visibility"]')) r.onchange = () => { $("#vis-help").textContent = r.value === "public" ? "Public: anyone can find and use this voice in the library." : "Private: only your account can use this voice."; };
  let mr = null, chunks = [], t0 = 0, tick = 0;
  $("#rec").onclick = async () => {
    if (mr) { mr.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      mr = new MediaRecorder(stream); chunks = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); clearInterval(tick); const b = new Blob(chunks, { type: mr.mimeType || "audio/webm" }); mr = null;
        $("#rec").innerHTML = "Record again"; $("#rec-st").textContent = ""; setFile(b, `Recording, ${((Date.now() - t0) / 1000).toFixed(0)} s`); };
      mr.start(); t0 = Date.now(); $("#rec").innerHTML = '<span class="recdot"></span> Stop';
      tick = setInterval(() => { const s = (Date.now() - t0) / 1000; $("#rec-st").textContent = `${s.toFixed(0)} s ${s < 10 ? "· keep going" : s > 20 ? "· that's plenty" : "· great"}`; if (s > 30) mr?.stop(); }, 250);
    } catch { toast("Could not use the microphone. Upload a file instead.", "bad"); }
  };
  $("#cf").onsubmit = (e) => {
    e.preventDefault();
    if (!recBlob) return toast("Add a recording first.", "bad");
    const fd = new FormData(e.target); fd.set("file", recBlob, recBlob.name || "recording.webm");
    busy($("#cf button[type=submit]"), async () => {
      const r = await api("POST", "/api/me/voices", fd, true);
      toast(`You're #${r.queue_position} in the queue. We'll email you when "${r.name}" is ready.`, "ok");
      e.target.reset(); setFile(null); S.me = await api("GET", "/api/me"); await loadMine(); renderMine();
    });
  };
}
function statusTag(v, busyWord = "Cloning now") {
  return { queued: `<span class="tag warn"><span class="dot pulse"></span>In queue${v.queue_position ? " · #" + v.queue_position : ""}</span>`, processing: `<span class="tag a"><span class="dot pulse"></span>${busyWord}</span>`,
    ready: '<span class="tag ok">Ready</span>', failed: '<span class="tag bad">Failed</span>' }[v.status] || esc(v.status);
}
let mineTimer = 0;
function renderMine() {
  const el = $("#mine"); if (!el) return;
  el.innerHTML = S.mine.length ? S.mine.map((v) => `<div class="li"><div class="av" data-hue="${hue(v.id)}">${esc(v.name[0])}</div>
    <div class="grow"><b>${esc(v.name)}</b> ${statusTag(v)}<div class="small muted"><code>${esc(v.id)}</code> · ${v.visibility === "public" ? "🌍 Public" : "🔒 Private"}${v.hidden ? " · hidden by admin" : ""}${v.error ? " · " + esc(v.error.slice(0, 80)) : ""}</div></div>
    ${v.status === "ready" ? `<button class="btn sm" data-mp="${esc(v.id)}">▶</button><button class="btn sm" data-mv="${esc(v.id)}" data-to="${v.visibility === "public" ? "private" : "public"}">Make ${v.visibility === "public" ? "private" : "public"}</button><button class="btn sm" data-mu="${esc(v.id)}">Use</button>` : ""}
    ${v.status !== "processing" ? `<button class="btn sm danger" data-md="${esc(v.id)}" aria-label="Delete">✕</button>` : ""}</div>`).join("")
    : '<div class="empty small">No voices yet. Your clones show up here.</div>';
  for (const a of $$("[data-hue]", el)) a.style.setProperty("--h", a.dataset.hue);
  for (const b of $$("[data-mp]", el)) b.onclick = () => { const v = voiceById(b.dataset.mp); playTrack({ src: v.sample, title: v.name, sub: "Your cloned voice", h: hue(v.id) }); };
  for (const b of $$("[data-mu]", el)) b.onclick = () => { sessionStorage.setItem("tfe_voice", b.dataset.mu); location.hash = "#/studio"; };
  for (const b of $$("[data-mv]", el)) b.onclick = () => busy(b, async () => { await api("PATCH", `/api/me/voices/${b.dataset.mv}`, { visibility: b.dataset.to }); await loadMine(); renderMine(); loadVoices(); toast(`Now ${b.dataset.to}.`, "ok"); });
  for (const b of $$("[data-md]", el)) b.onclick = () => { if (confirm("Delete this voice for good?")) busy(b, async () => { await api("DELETE", `/api/me/voices/${b.dataset.md}`); await loadMine(); renderMine(); }); };
  clearTimeout(mineTimer); clearTimeout(musicTimer);
  if (S.mine.some((v) => v.status === "queued" || v.status === "processing")) mineTimer = setTimeout(async () => { if (!$("#mine")) return; const before = S.mine.filter((v) => v.status === "ready").length; await loadMine(); if (S.mine.filter((v) => v.status === "ready").length > before) toast("Your voice is ready! 🎉", "ok"); renderMine(); }, 10000);
}

/* ------------------------------------------------------------ router */
async function route() {
  preview.stop();
  const name = (location.hash.replace(/^#\/?/, "").split("?")[0] || "home");
  if (name === "voices") { location.hash = "#/"; return; }
  const fn = pages[name] || pages.home;
  for (const a of $$("#nav a")) a.classList.toggle("on", a.dataset.r === name);
  try { await fn(); } catch (e) { view.innerHTML = `<div class="auth card"><h1>Oops</h1><p class="muted">${esc(e.message)}</p></div>`; }
  if (!location.hash.includes("voices")) window.scrollTo({ top: 0 });
  view.focus({ preventScroll: true });
}
document.addEventListener("click", (e) => { const a = e.target.closest('a[href="#voices"]'); if (a) { e.preventDefault(); $("#voices")?.scrollIntoView({ behavior: "smooth" }); } });
addEventListener("hashchange", route);
(async () => { await Promise.all([loadMe(), loadVoices()]); if (S.me) await loadMine().catch(() => {}); route(); })();
