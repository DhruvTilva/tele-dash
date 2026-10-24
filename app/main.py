import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from telethon import errors

from .accounts import Accounts, explain
from .sources import DemoSource

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
PASSWORD = os.getenv("APP_PASSWORD", "")      # optional invite password for the whole site
DEMO = os.getenv("DEMO") == "1"
SHARED_API = (os.getenv("TG_API_ID", ""), os.getenv("TG_API_HASH", ""))  # optional default API keys for everyone
OPEN_ENDED_CAP = 32 * 1024 * 1024  # answer "bytes=N-" requests in 32 MB slices
GATE_COOKIE, SESSION_COOKIE, SETUP_COOKIE = "teledash_gate", "teledash_sess", "teledash_setup"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_FILE = DATA_DIR / "secret.key"
if PASSWORD:  # derived from the password, so logins survive restarts on hosts that wipe the disk
    SECRET = hashlib.sha256(("tele-dash:" + PASSWORD).encode()).digest()
else:
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        os.chmod(SECRET_FILE, 0o600)
    SECRET = SECRET_FILE.read_text().strip().encode()
progress_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    app.state.accounts = Accounts(DATA_DIR)
    app.state.demo = DemoSource(DATA_DIR) if DEMO else None
    if app.state.demo:
        await app.state.demo.start()
    yield
    for src in app.state.accounts.live.values():
        await src.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------- access: optional invite password + per-person Telegram sign-in ----------

def sign(value):
    return hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()


def gate_ok(request):
    return not PASSWORD or hmac.compare_digest(request.cookies.get(GATE_COOKIE, ""), sign("gate:" + PASSWORD))


def session_uid(request):
    uid, _, mac = request.cookies.get(SESSION_COOKIE, "").partition(".")
    return uid if uid.isdigit() and hmac.compare_digest(mac, sign("uid:" + uid)) else None


def set_cookie(resp, request, name, value, days=90):
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(name, value, httponly=True, samesite="lax", secure=secure, max_age=days * 86400)


async def current_source(request: Request):
    """The signed-in person's Telegram source (the dependency for every data endpoint)."""
    if DEMO:
        return app.state.demo
    uid = session_uid(request)
    src = await app.state.accounts.get(uid) if uid else None
    if not src:
        raise HTTPException(401, "Sign in to Telegram first")
    return src


Source = Depends(current_source)


@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    open_path = p in ("/api/auth", "/api/login")
    protected = p.startswith(("/stream/", "/thumb/")) or (p.startswith("/api/") and not open_path)
    if protected and not gate_ok(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
async def auth_status(request: Request):
    ok = gate_ok(request)
    uid = session_uid(request) if ok else None
    src = (app.state.demo if DEMO else await app.state.accounts.get(uid)) if (ok and (DEMO or uid)) else None
    return {"gate": bool(PASSWORD), "gate_ok": ok, "signed_in": bool(src), "demo": DEMO, "uid": uid if src else "",
            "name": "Demo" if DEMO else (app.state.accounts.name(uid) if src else ""),
            "shared_api": all(SHARED_API)}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if not PASSWORD or hmac.compare_digest(str(body.get("password", "")), PASSWORD):
        resp = JSONResponse({"ok": True})
        set_cookie(resp, request, GATE_COOKIE, sign("gate:" + PASSWORD))
        return resp
    await asyncio.sleep(1)  # slow down password guessing
    return JSONResponse({"error": "wrong password"}, status_code=401)


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    for c in (GATE_COOKIE, SESSION_COOKIE, SETUP_COOKIE):
        resp.delete_cookie(c)
    return resp


def signed_in_response(request, uid):
    resp = JSONResponse({"ok": True, "step": "done"})
    set_cookie(resp, request, SESSION_COOKIE, f"{uid}.{sign('uid:' + uid)}")
    resp.delete_cookie(SETUP_COOKIE)
    return resp


async def run_step(coro):
    try:
        return await coro
    except (errors.RPCError, ValueError) as e:
        raise HTTPException(400, explain(e))


@app.post("/api/tg/start")
async def tg_start(request: Request):
    body = await request.json()
    api_id = body.get("api_id") or SHARED_API[0]
    api_hash = body.get("api_hash") or SHARED_API[1]
    token = await run_step(app.state.accounts.begin(api_id, api_hash, body.get("phone", "")))
    resp = JSONResponse({"ok": True, "step": "code"})
    set_cookie(resp, request, SETUP_COOKIE, token, days=1)
    return resp


@app.post("/api/tg/code")
async def tg_code(request: Request):
    body = await request.json()
    uid = await run_step(app.state.accounts.code(request.cookies.get(SETUP_COOKIE), body.get("code", "")))
    return signed_in_response(request, uid) if uid else {"ok": True, "step": "password"}


@app.post("/api/tg/password")
async def tg_password(request: Request):
    body = await request.json()
    uid = await run_step(app.state.accounts.password(request.cookies.get(SETUP_COOKIE), body.get("password", "")))
    return signed_in_response(request, uid)


@app.get("/api/tg/session")
async def tg_session(request: Request, src=Source):
    return app.state.accounts.session_info(session_uid(request))


@app.post("/api/tg/restore")
async def tg_restore(request: Request):
    body = await request.json()
    uid = await run_step(app.state.accounts.restore(
        body.get("api_id"), body.get("api_hash"), body.get("session"), body.get("progress"), body.get("prefs")))
    return signed_in_response(request, uid)


@app.post("/api/account/delete")
async def account_delete(request: Request):
    uid = session_uid(request)
    if uid:
        await app.state.accounts.delete(uid)
    return await logout()


# ---------- data ----------

@app.get("/api/channels")
async def channels(src=Source):
    return await src.channels()


@app.get("/api/channels/{cid}/items")
async def items(cid: int, refresh: str = "", src=Source):
    """refresh='' use cache, '1' fetch only new messages, 'full' rescan the whole channel."""
    if refresh == "":
        cache = src.data_dir / "items" / f"{cid}.json"
        if cache.exists():
            return json.loads(cache.read_text())
    return await src.items(cid, full=refresh == "full")


@app.get("/api/prefs")
async def get_prefs(src=Source):
    f = src.data_dir / "prefs.json"
    return json.loads(f.read_text()) if f.exists() else {"channels": []}


@app.post("/api/prefs")
async def set_prefs(request: Request, src=Source):
    body = await request.json()
    prefs = {"channels": [int(c) for c in body.get("channels", [])]}
    (src.data_dir / "prefs.json").write_text(json.dumps(prefs))
    return prefs


@app.get("/api/progress")
async def get_progress(src=Source):
    f = src.data_dir / "progress.json"
    return json.loads(f.read_text()) if f.exists() else {}


@app.post("/api/progress")
async def set_progress(request: Request, src=Source):
    body = await request.json()
    f = src.data_dir / "progress.json"
    async with progress_lock:
        data = json.loads(f.read_text()) if f.exists() else {}
        data[str(body["key"])] = {"t": float(body.get("t", 0)), "d": float(body.get("d", 0)), "done": bool(body.get("done"))}
        f.write_text(json.dumps(data))
    return {"ok": True}


# ---------- media ----------

def parse_range(header, size):
    if not header:
        return 0, size - 1, False
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
    s, e = m.groups()
    if not s:
        start, end = max(size - int(e), 0), size - 1
    else:
        start = int(s)
        end = min(int(e), size - 1) if e else min(start + OPEN_ENDED_CAP - 1, size - 1)
    if start > end or start >= size:
        raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
    return start, end, True


@app.api_route("/stream/{cid}/{mid}", methods=["GET", "HEAD"])
async def stream(cid: int, mid: int, request: Request, download: int = 0, src=Source):
    try:
        meta = await src.meta(cid, mid)
    except KeyError:
        raise HTTPException(404)
    size = meta["size"]
    start, end, partial = parse_range(request.headers.get("range"), size)
    name = quote(meta["name"])
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"{'attachment' if download else 'inline'}; filename*=UTF-8''{name}",
        "Cache-Control": "private, max-age=3600",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    status = 206 if partial else 200
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=meta["mime"])
    return StreamingResponse(src.stream(cid, mid, start, end), status_code=status, headers=headers,
                             media_type=meta["mime"])


@app.get("/thumb/{cid}/{mid}")
async def thumb(cid: int, mid: int, src=Source):
    try:
        data = await src.thumb(cid, mid)
    except KeyError:
        data = None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
