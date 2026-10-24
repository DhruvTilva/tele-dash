"""Where videos come from: real Telegram (TelegramSource) or fake demo data (DemoSource)."""
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path

CHUNK = 1024 * 1024  # Telegram wants offsets aligned to 1 MB chunks

TITLE_RE = re.compile(r"Title\s*[»>:\-–]+\s*(.+)", re.I)
EXT_RE = re.compile(r"\.(mp4|mkv|webm|mov|avi|pdf|zip|rar|pptx?|docx?|xlsx?|txt|ipynb)$", re.I)


def clean_title(caption, filename, msg_id):
    """Pick a readable title: 'Title » ...' line of the caption, else file name."""
    title = ""
    if caption:
        m = TITLE_RE.search(caption)
        title = m.group(1).strip() if m else caption.strip().splitlines()[0]
    if not title:
        title = filename or f"Message {msg_id}"
    return EXT_RE.sub("", title).strip() or f"Message {msg_id}"


def kind_of(mime):
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf":
        return "pdf"
    return "file"


class TelegramSource:
    demo = False

    def __init__(self, client, data_dir):
        self.client = client
        self.data_dir = Path(data_dir)
        (self.data_dir / "items").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "thumbs").mkdir(parents=True, exist_ok=True)
        self._msgs = {}
        self._dialogs = (0, [])

    async def start(self):
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session is no longer valid. Sign in again.")

    async def stop(self):
        await self.client.disconnect()

    async def channels(self):
        ts, cached = self._dialogs
        if time.time() - ts < 60:
            return cached
        out = []
        async for d in self.client.iter_dialogs():
            if d.is_channel or d.is_group:
                out.append({"id": d.id, "title": d.name})
        self._dialogs = (time.time(), out)
        return out

    async def _entity(self, chan):
        try:
            return await self.client.get_input_entity(chan)
        except ValueError:
            self._dialogs = (0, [])
            await self.channels()  # fills the entity cache
            return await self.client.get_input_entity(chan)

    def _item(self, m):
        if not m.document or m.sticker or m.gif or m.voice:
            return None
        mime = m.file.mime_type or "application/octet-stream"
        if mime.startswith("audio/"):
            return None
        return {
            "id": m.id,
            "title": clean_title(m.message, m.file.name, m.id),
            "kind": kind_of(mime),
            "mime": mime,
            "size": m.file.size or 0,
            "duration": int(m.file.duration or 0),
            "date": m.date.isoformat() if m.date else "",
            "thumb": bool(getattr(m.document, "thumbs", None)),
        }

    async def items(self, chan, full=False):
        cache = self.data_dir / "items" / f"{chan}.json"
        items = []
        if cache.exists() and not full:
            items = json.loads(cache.read_text())
        last = max((i["id"] for i in items), default=0)
        entity = await self._entity(chan)
        async for m in self.client.iter_messages(entity, min_id=last, reverse=True):
            it = self._item(m)
            if it:
                items.append(it)
        cache.write_text(json.dumps(items))
        return items

    async def _msg(self, chan, msg_id):
        hit = self._msgs.get((chan, msg_id))
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        m = await self.client.get_messages(await self._entity(chan), ids=msg_id)
        if not m or not m.document:
            raise KeyError(msg_id)
        if len(self._msgs) > 64:
            self._msgs.clear()
        self._msgs[(chan, msg_id)] = (time.time(), m)
        return m

    async def meta(self, chan, msg_id):
        m = await self._msg(chan, msg_id)
        return {"size": m.file.size, "mime": m.file.mime_type or "application/octet-stream",
                "name": m.file.name or f"{msg_id}"}

    async def stream(self, chan, msg_id, start, end):
        m = await self._msg(chan, msg_id)
        aligned = start - start % CHUNK
        skip = start - aligned
        remaining = end - start + 1
        async with self.client.iter_download(
            m.document, offset=aligned, request_size=CHUNK, file_size=m.file.size
        ) as it:
            async for chunk in it:
                if skip:
                    chunk, skip = chunk[skip:], 0
                chunk = chunk[:remaining]
                remaining -= len(chunk)
                yield bytes(chunk)
                if remaining <= 0:
                    break

    async def thumb(self, chan, msg_id):
        path = self.data_dir / "thumbs" / f"{chan}_{msg_id}.jpg"
        if path.exists():
            return path.read_bytes()
        m = await self._msg(chan, msg_id)
        data = await self.client.download_media(m, file=bytes, thumb=-1)
        if data:
            path.write_bytes(data)
        return data or None


class DemoSource:
    """Fake channels with generated sample videos so the UI can be tried without Telegram."""
    demo = True

    def __init__(self, data_dir):
        self.dir = Path(data_dir) / "demo"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir) / "users" / "demo"
        (self.data_dir / "items").mkdir(parents=True, exist_ok=True)
        self.video = self.dir / "sample.mp4"
        self.pdf = self.dir / "sample.pdf"

    async def start(self):
        if not self.video.exists():
            await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24",
                 "-f", "lavfi", "-i", "sine=frequency=440", "-t", "60", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", str(self.video)],
                check=True,
            )
        if not self.pdf.exists():
            self.pdf.write_bytes(
                b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
            )

    async def stop(self):
        pass

    async def channels(self):
        return [{"id": -1001, "title": "Demo Course - Data Science"}, {"id": -1002, "title": "Demo Course - Python"}]

    async def items(self, chan, full=False):
        titles = ["Orientation Session", "Prerequisites", "Python Basics", "NumPy Deep Dive", "Pandas Part 1",
                  "Pandas Part 2", "Day 7 - Visualization", "Day 8 - Statistics", "Day 9 - Probability", "Day 10 - EDA Project"]
        size = self.video.stat().st_size
        out = []
        for n, t in enumerate(titles, 1):
            if n == 2:
                out.append({"id": n, "title": t + " (PDF)", "kind": "pdf", "mime": "application/pdf",
                            "size": self.pdf.stat().st_size, "duration": 0, "date": "", "thumb": False})
            else:
                out.append({"id": n, "title": t, "kind": "video", "mime": "video/mp4", "size": size,
                            "duration": 60, "date": "", "thumb": False})
        return out

    async def meta(self, chan, msg_id):
        if msg_id == 2:
            return {"size": self.pdf.stat().st_size, "mime": "application/pdf", "name": "sample.pdf"}
        return {"size": self.video.stat().st_size, "mime": "video/mp4", "name": f"{msg_id}.mp4"}

    async def stream(self, chan, msg_id, start, end):
        path = self.pdf if msg_id == 2 else self.video
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    async def thumb(self, chan, msg_id):
        return None
