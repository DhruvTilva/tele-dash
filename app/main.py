import asyncio
import hashlib
import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .sources import DemoSource, TelegramSource

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
PASSWORD = os.getenv("APP_PASSWORD", "")
OPEN_ENDED_CAP = 16 * 1024 * 1024  # answer "bytes=N-" requests in 16 MB slices
COOKIE = "teledash_auth"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = DATA_DIR / "progress.json"
progress_lock = asyncio.Lock()


def make_source():
    if os.getenv("DEMO") == "1":
        return DemoSource(DATA_DIR)
    api_id, api_hash = os.getenv("TG_API_ID"), os.getenv("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("Set TG_API_ID and TG_API_HASH in .env (see .env.example), or run with DEMO=1.")
    return TelegramSource(int(api_id), api_hash, DATA_DIR, os.getenv("TG_SESSION_STRING", ""))


@asynccontextmanager
async def lifespan(app):
    app.state.src = make_source()
    await app.state.src.start()
    yield
    await app.state.src.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------- auth (only when APP_PASSWORD is set) ----------

def token():
    return hmac.new(PASSWORD.encode(), b"tele-dash", hashlib.sha256).hexdigest()


def authed(request):
    return not PASSWORD or hmac.compare_digest(request.cookies.get(COOKIE, ""), token())


@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    protected = p.startswith(("/stream/", "/thumb/")) or (p.startswith("/api/") and p not in ("/api/auth", "/api/login"))
    if protected and not authed(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
async def auth_status(request: Request):
    return {"required": bool(PASSWORD), "ok": authed(request), "demo": app.state.src.demo}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if not PASSWORD or hmac.compare_digest(str(body.get("password", "")), PASSWORD):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token() if PASSWORD else "", httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 90)
        return resp
    await asyncio.sleep(1)  # slow down password guessing
    return JSONResponse({"error": "wrong password"}, status_code=401)


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# ---------- data ----------

@app.get("/api/channels")
async def channels():
    return await app.state.src.channels()


@app.get("/api/channels/{cid}/items")
async def items(cid: int, refresh: str = ""):
    """refresh='' use cache, '1' fetch only new messages, 'full' rescan the whole channel."""
    src = app.state.src
    if refresh == "":
        cache = DATA_DIR / "items" / f"{cid}.json"
        if cache.exists():
            return json.loads(cache.read_text())
    return await src.items(cid, full=refresh == "full")


@app.get("/api/progress")
async def get_progress():
    return json.loads(PROGRESS_FILE.read_text()) if PROGRESS_FILE.exists() else {}


@app.post("/api/progress")
async def set_progress(request: Request):
    body = await request.json()
    key = str(body["key"])
    async with progress_lock:
        data = json.loads(PROGRESS_FILE.read_text()) if PROGRESS_FILE.exists() else {}
        data[key] = {"t": float(body.get("t", 0)), "d": float(body.get("d", 0)), "done": bool(body.get("done"))}
        PROGRESS_FILE.write_text(json.dumps(data))
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
async def stream(cid: int, mid: int, request: Request, download: int = 0):
    src = app.state.src
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
async def thumb(cid: int, mid: int):
    try:
        data = await app.state.src.thumb(cid, mid)
    except KeyError:
        data = None
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
