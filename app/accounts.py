"""Per-person Telegram accounts: sign-in wizard (api keys -> phone -> code -> 2FA) and stored sessions."""
import asyncio
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from .sources import TelegramSource

PENDING_TTL = 600

FRIENDLY = {
    "PhoneNumberInvalidError": "That phone number isn't valid. Use the + country code format, e.g. +919876543210.",
    "ApiIdInvalidError": "API ID or API hash is wrong. Copy both again from my.telegram.org.",
    "PhoneCodeInvalidError": "Wrong code. Try again.",
    "PhoneCodeExpiredError": "That code expired. Go back and request a new one.",
    "PasswordHashInvalidError": "Wrong two-step verification password.",
}


def explain(e):
    if isinstance(e, errors.FloodWaitError):
        return f"Too many attempts. Try again in {e.seconds} seconds."
    if isinstance(e, ValueError):
        return str(e)
    return FRIENDLY.get(type(e).__name__) or (str(e) or "Telegram refused that.")


class Accounts:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / "users"
        self.root.mkdir(parents=True, exist_ok=True)
        self.live = {}      # uid -> TelegramSource
        self.pending = {}   # setup token -> half-finished sign-in
        self.lock = asyncio.Lock()

    def user_dir(self, uid):
        return self.root / uid

    def name(self, uid):
        try:
            return json.loads((self.user_dir(uid) / "account.json").read_text()).get("name", "")
        except (OSError, ValueError):
            return ""

    async def get(self, uid):
        """The connected source for a signed-in user, or None."""
        if uid in self.live:
            return self.live[uid]
        async with self.lock:
            if uid in self.live:
                return self.live[uid]
            f = self.user_dir(uid) / "account.json"
            if not f.exists():
                return None
            acc = json.loads(f.read_text())
            client = TelegramClient(StringSession(acc["session"]), int(acc["api_id"]), acc["api_hash"])
            src = TelegramSource(client, self.user_dir(uid))
            try:
                await src.start()
            except Exception:
                await client.disconnect()
                return None
            self.live[uid] = src
            return src

    # ----- sign-in wizard -----

    async def _expire(self):
        for tok, p in list(self.pending.items()):
            if time.time() - p["ts"] > PENDING_TTL:
                self.pending.pop(tok, None)
                await p["client"].disconnect()

    def _pending(self, token):
        p = self.pending.get(token or "")
        if not p or time.time() - p["ts"] > PENDING_TTL:
            raise ValueError("Sign-in expired. Please start again.")
        return p

    async def begin(self, api_id, api_hash, phone):
        await self._expire()
        try:
            api_id = int(str(api_id).strip())
        except ValueError:
            raise ValueError("API ID must be a number.")
        api_hash = str(api_hash).strip()
        phone = str(phone).strip().replace(" ", "")
        if not api_hash or not phone.startswith("+"):
            raise ValueError("Enter your API hash and phone number with country code (+91...).")
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except Exception:
            await client.disconnect()
            raise
        token = secrets.token_urlsafe(24)
        self.pending[token] = {"client": client, "phone": phone, "hash": sent.phone_code_hash,
                               "api_id": api_id, "api_hash": api_hash, "ts": time.time()}
        return token

    async def code(self, token, code):
        """Returns the user id, or None when two-step verification asks for a password."""
        p = self._pending(token)
        try:
            await p["client"].sign_in(p["phone"], str(code).strip(), phone_code_hash=p["hash"])
        except errors.SessionPasswordNeededError:
            return None
        return await self._finish(token)

    async def password(self, token, password):
        p = self._pending(token)
        await p["client"].sign_in(password=password)
        return await self._finish(token)

    async def _finish(self, token):
        p = self.pending.pop(token)
        client = p["client"]
        me = await client.get_me()
        uid = str(me.id)
        d = self.user_dir(uid)
        d.mkdir(parents=True, exist_ok=True)
        name = " ".join(x for x in (me.first_name, me.last_name) if x) or me.username or uid
        f = d / "account.json"
        f.write_text(json.dumps({"api_id": p["api_id"], "api_hash": p["api_hash"],
                                 "session": client.session.save(), "name": name}))
        os.chmod(f, 0o600)
        old = self.live.pop(uid, None)
        if old:
            await old.stop()
        self.live[uid] = TelegramSource(client, d)
        return uid

    async def delete(self, uid):
        """Revoke the Telegram session and wipe everything stored for this user."""
        src = self.live.pop(uid, None) or await self.get(uid)
        if src:
            try:
                await src.client.log_out()
            except Exception:
                pass
            await src.stop()
        shutil.rmtree(self.user_dir(uid), ignore_errors=True)
