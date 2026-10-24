const $ = (id) => document.getElementById(id);
const S = { chan: null, items: [], progress: {}, filter: "all", current: null, lastSave: 0 };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.id); toast.id = setTimeout(() => t.classList.remove("show"), 3000);
}
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) { init(); throw new Error("Please sign in"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
const fmtTime = (s) => {
  s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
};
const fmtSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1e3)) + " KB";
const key = (it) => `${S.chan.id}:${it.id}`;

/* ---------- access: password gate, Telegram setup, settings ---------- */
const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const show = (id, on) => { $(id).hidden = !on; };

$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  const r = await post("/api/login", { password: $("pw").value });
  if (r.ok) { $("loginErr").textContent = ""; init(); }
  else $("loginErr").textContent = "Wrong password";
};

let setupStep = 1, sharedApi = false;
function setStep(n) {
  setupStep = n;
  show("s1", n === 1); show("s2", n === 2); show("s3", n === 3); show("setupBack", n > 1);
  $("setupBtn").textContent = ["", "Send code", "Verify code", "Sign in"][n];
  $("setupErr").textContent = "";
  $(["", "phone", "code", "tgPassword"][n]).focus();
}
function showSetup() {
  show("setup", true);
  show("apiFields", !sharedApi); show("sharedNote", sharedApi);
  setStep(1);
}
$("ownKeys").onclick = (e) => { e.preventDefault(); sharedApi = false; show("apiFields", true); show("sharedNote", false); };
$("setupBack").onclick = () => setStep(1);
$("setupForm").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("setupBtn"); btn.disabled = true; $("setupErr").textContent = "";
  try {
    const url = ["", "/api/tg/start", "/api/tg/code", "/api/tg/password"][setupStep];
    const body = setupStep === 1 ? { api_id: $("apiId").value, api_hash: $("apiHash").value, phone: $("phone").value }
      : setupStep === 2 ? { code: $("code").value } : { password: $("tgPassword").value };
    const r = await post(url, body), j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || "Something went wrong");
    if (j.step === "code") setStep(2);
    else if (j.step === "password") setStep(3);
    else if (j.step === "done") { ["code", "tgPassword"].forEach((i) => ($(i).value = "")); show("setup", false); init(); }
  } catch (err) { $("setupErr").textContent = err.message; }
  btn.disabled = false;
};

$("settingsBtn").onclick = () => { show("settings", true); document.body.classList.remove("drawer"); };
$("settingsClose").onclick = () => show("settings", false);
$("logoutBtn").onclick = async () => { await post("/api/logout"); location.reload(); };
$("deleteBtn").onclick = async () => {
  if (!confirm("Disconnect Telegram and delete your saved progress from this server?")) return;
  await post("/api/account/delete"); location.reload();
};

/* ---------- channels ---------- */
let channels = [];
function renderChannels() {
  const q = $("chanSearch").value.toLowerCase();
  const box = $("channels"); box.replaceChildren();
  for (const c of channels.filter((c) => c.title.toLowerCase().includes(q))) {
    const b = el("button", S.chan && S.chan.id === c.id ? "on" : "", c.title);
    b.onclick = () => { selectChannel(c); document.body.classList.remove("drawer"); };
    box.append(b);
  }
  if (!box.children.length) box.append(el("div", "empty", "No channels found"));
}
$("chanSearch").oninput = renderChannels;

async function selectChannel(c) {
  S.chan = c; store.set("chan:" + $("whoami").textContent, c.id);
  $("courseTitle").textContent = c.title;
  S.items = []; stopPlayer(); renderChannels(); renderItems();
  $("summary").textContent = "Loading...";
  try { S.items = await api(`/api/channels/${c.id}/items`); } catch (e) { toast(e.message); }
  renderItems();
  const last = store.get("last:" + c.id);
  const it = S.items.find((i) => String(i.id) === last);
  if (it && it.kind === "video") play(it, false);
}

$("sync").onclick = async () => {
  if (!S.chan) return;
  $("sync").classList.add("spin");
  try {
    const before = S.items.length;
    S.items = await api(`/api/channels/${S.chan.id}/items?refresh=1`);
    renderItems();
    toast(S.items.length > before ? `${S.items.length - before} new item(s)` : "Already up to date");
  } catch (e) { toast(e.message); }
  $("sync").classList.remove("spin");
};

/* ---------- playlist ---------- */
const natural = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
function visibleItems() {
  const q = $("search").value.toLowerCase(), f = S.filter, sort = $("sort").value;
  let list = S.items.map((it, i) => ({ ...it, seq: i + 1 }));
  list = list.filter((it) => {
    if (q && !it.title.toLowerCase().includes(q)) return false;
    if (f === "video" || f === "pdf") return it.kind === f;
    if (f === "todo") return it.kind === "video" && !(S.progress[key(it)] || {}).done;
    return true;
  });
  if (sort === "new") list.reverse();
  if (sort === "title") list.sort((a, b) => natural.compare(a.title, b.title));
  return list;
}

function renderItems() {
  const ul = $("items"); ul.replaceChildren();
  const list = visibleItems();
  const videos = S.items.filter((i) => i.kind === "video");
  const done = S.chan ? videos.filter((i) => (S.progress[key(i)] || {}).done).length : 0;
  $("summary").textContent = S.chan ? `${videos.length} videos · ${done} watched · showing ${list.length}` : "";
  if (!list.length) { ul.append(el("li", "empty", S.chan ? "Nothing here" : "Choose a channel from the menu")); return; }
  for (const it of list) {
    const p = S.progress[key(it)] || {};
    const li = el("li", "item" + (S.current && S.current.id === it.id ? " playing" : ""));
    li.dataset.id = it.id;
    li.append(el("div", "num", it.seq));
    const th = el("div", "thumb", it.kind === "video" ? "▶" : it.kind === "pdf" ? "📄" : "📎");
    if (it.thumb) {
      const img = el("img"); img.loading = "lazy"; img.alt = "";
      img.src = `/thumb/${S.chan.id}/${it.id}`; img.onerror = () => img.remove();
      th.append(img);
    }
    li.append(th);
    const info = el("div", "info");
    info.append(el("div", "t", it.title));
    const meta = [it.kind === "video" && it.duration ? fmtTime(it.duration) : it.kind.toUpperCase(), fmtSize(it.size)];
    info.append(el("div", "m", meta.join(" · ")));
    li.append(info);
    if (p.done) li.append(el("div", "done", "✓"));
    else if (p.d && p.t > 5) {
      const bar = el("div", "bar"), i = el("i"); i.style.width = Math.min(100, p.t / p.d * 100) + "%";
      bar.append(i); li.append(bar);
    }
    li.onclick = () => open(it);
    ul.append(li);
  }
}
$("search").oninput = renderItems;
$("sort").onchange = () => { store.set("sort", $("sort").value); renderItems(); };
$("chips").onclick = (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  S.filter = b.dataset.f;
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  renderItems();
};

/* ---------- player ---------- */
const video = $("video");
function open(it) {
  if (it.kind === "video") return play(it, true);
  if (it.kind === "pdf") return openPdf(it);
  window.open(`/stream/${S.chan.id}/${it.id}?download=1`, "_blank");
}

/* ---------- PDF viewer (PDF.js, bundled in web/vendor) ---------- */
let pdfRun = 0;
async function downloadBytes(url, onProgress) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status);
  const total = +r.headers.get("Content-Length") || 0, chunks = [];
  let got = 0;
  const reader = r.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length;
    if (total) onProgress(Math.round(got / total * 100));
  }
  const out = new Uint8Array(got); let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}
async function openPdf(it) {
  const run = ++pdfRun, box = $("pdfPages"), status = $("pdfStatus");
  const url = `/stream/${S.chan.id}/${it.id}`;
  $("pdfTitle").textContent = it.title;
  $("pdfDownload").href = url + "?download=1";
  box.replaceChildren(); status.textContent = "Loading PDF...";
  show("pdfView", true);
  try {
    pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdfjs/pdf.worker.min.js";
    const data = await downloadBytes(url, (pct) => { if (run === pdfRun) status.textContent = `Downloading PDF... ${pct}%`; });
    if (run !== pdfRun) return;
    const doc = await pdfjsLib.getDocument({ data }).promise;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    for (let n = 1; n <= doc.numPages; n++) {
      status.textContent = `Rendering page ${n} of ${doc.numPages}...`;
      const page = await doc.getPage(n);
      if (run !== pdfRun) return;
      const width = box.clientWidth - 16;
      const scale = Math.min(width / page.getViewport({ scale: 1 }).width, 3);
      const vp = page.getViewport({ scale: scale * dpr });
      const canvas = el("canvas");
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.style.width = vp.width / dpr + "px";
      box.append(canvas);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    }
    if (run === pdfRun) status.textContent = "";
  } catch (e) {
    if (run === pdfRun) status.textContent = `Can't show this PDF (${e.message || e}). Use Download.`;
  }
}
function closePdf() { pdfRun++; show("pdfView", false); $("pdfPages").replaceChildren(); }
$("pdfClose").onclick = closePdf;
function play(it, autostart) {
  saveProgress(true);
  S.current = it; store.set("last:" + S.chan.id, it.id);
  $("nowTitle").textContent = it.title;
  document.querySelector(".player").classList.add("has");
  video.src = `/stream/${S.chan.id}/${it.id}`;
  video.playbackRate = parseFloat($("speed").value);
  const p = S.progress[key(it)];
  video.onloadedmetadata = () => {
    if (p && !p.done && p.t > 5 && p.t < video.duration - 10) video.currentTime = p.t;
    video.playbackRate = parseFloat($("speed").value);
  };
  if (autostart) video.play().catch(() => {});
  renderItems();
  document.querySelector(".item.playing")?.scrollIntoView({ block: "nearest" });
  if (window.matchMedia("(max-width:1000px)").matches) document.querySelector(".layout").scrollTo({ top: 0, behavior: "smooth" });
}
function stopPlayer() {
  saveProgress(true); S.current = null; video.removeAttribute("src"); video.load();
  $("nowTitle").textContent = "—"; document.querySelector(".player").classList.remove("has");
}
function step(dir) {
  const vids = visibleItems().filter((i) => i.kind === "video");
  const i = vids.findIndex((v) => S.current && v.id === S.current.id);
  const n = vids[i + dir];
  if (n) play(n, true); else toast(dir > 0 ? "That was the last video" : "This is the first video");
}
$("next").onclick = () => step(1);
$("prev").onclick = () => step(-1);
$("speed").onchange = () => { video.playbackRate = parseFloat($("speed").value); store.set("speed", $("speed").value); };
$("autoplay").onchange = () => store.set("autoplay", $("autoplay").checked ? "1" : "0");

function saveProgress(force, done) {
  if (!S.current || !video.duration || !isFinite(video.duration)) return;
  const now = Date.now();
  if (!force && now - S.lastSave < 5000) return;
  S.lastSave = now;
  const t = video.currentTime, d = video.duration;
  const isDone = done || t > d * 0.92 || (S.progress[key(S.current)] || {}).done;
  const rec = { t, d, done: isDone };
  S.progress[key(S.current)] = rec;
  fetch("/api/progress", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: key(S.current), ...rec }), keepalive: true }).catch(() => {});
}
video.ontimeupdate = () => saveProgress(false);
video.onpause = () => saveProgress(true);
video.onended = () => { saveProgress(true, true); renderItems(); if ($("autoplay").checked) step(1); };
video.onerror = () => { if (S.current) toast("Can't play this video in the browser (format or network problem)"); };
window.addEventListener("pagehide", () => saveProgress(true));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("pdfView").hidden) return closePdf();
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === "j") video.currentTime -= 10;
  else if (e.key === "l") video.currentTime += 10;
  else if (e.key === "n") step(1);
  else if (e.key === "p") step(-1);
  else if (e.key === " " && S.current) { e.preventDefault(); video.paused ? video.play() : video.pause(); }
});

/* ---------- drawer ---------- */
$("menu").onclick = () => document.body.classList.add("drawer");
$("scrim").onclick = () => document.body.classList.remove("drawer");

/* ---------- start ---------- */
let booting = false;
async function init() {
  if (booting) return;
  booting = true;
  try {
    const a = await fetch("/api/auth").then((r) => r.json());
    show("login", false); show("setup", false);
    if (!a.gate_ok) { show("login", true); $("pw").focus(); return; }
    sharedApi = a.shared_api;
    if (!a.signed_in) return showSetup();
    $("whoami").textContent = a.name;
    $("settingsBtn").hidden = a.demo;
    if (a.demo) toast("Demo mode: fake data");
    $("sort").value = store.get("sort") || "old";
    $("speed").value = store.get("speed") || "1";
    $("autoplay").checked = store.get("autoplay") !== "0";
    try {
      [channels, S.progress] = await Promise.all([api("/api/channels"), api("/api/progress")]);
    } catch (e) { toast(e.message); return; }
    renderChannels(); renderItems();
    const saved = channels.find((c) => String(c.id) === store.get("chan:" + a.name));
    if (saved) selectChannel(saved); else document.body.classList.add("drawer");
  } finally { booting = false; }
}
init();
