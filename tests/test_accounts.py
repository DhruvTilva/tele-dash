"""Sign-in flow, per-user isolation and password gate, with Telegram replaced by a fake client."""
import importlib
import types

import pytest
from fastapi.testclient import TestClient
from telethon import errors


class FakeStringSession:
    def __init__(self, s=""):
        self.s = s

    def save(self):
        return self.s


class FakeClient:
    logged_out = []

    def __init__(self, session, api_id, api_hash):
        self.session = session
        self.phone = session.s[5:] or None   # a restored session remembers whose it is
        self.connected = False

    async def connect(self): self.connected = True
    async def disconnect(self): self.connected = False
    def is_connected(self): return self.connected
    async def is_user_authorized(self): return bool(self.session.s)

    async def send_code_request(self, phone):
        if phone.startswith("+000"):
            raise errors.PhoneNumberInvalidError(request=None)
        self.phone = phone
        return types.SimpleNamespace(phone_code_hash="h")

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        if code is not None:
            if code != "12345":
                raise errors.PhoneCodeInvalidError(request=None)
            if self.phone.endswith("2fa"):
                raise errors.SessionPasswordNeededError(request=None)
        elif password != "secret":
            raise errors.PasswordHashInvalidError(request=None)
        self.session = FakeStringSession("sess-" + self.phone)

    async def get_me(self):
        digits = "".join(c for c in self.phone if c.isdigit()) or "1"
        return types.SimpleNamespace(id=int(digits[-6:]), first_name="User" + digits[-2:], last_name=None, username=None)

    async def iter_dialogs(self):
        yield types.SimpleNamespace(is_channel=True, is_group=False, id=-100123, name="My Course " + self.phone)

    async def log_out(self): FakeClient.logged_out.append(self.phone)


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    def build(password="", data_dir=None):
        monkeypatch.setenv("DATA_DIR", str(data_dir or tmp_path))
        monkeypatch.setenv("APP_PASSWORD", password)
        monkeypatch.delenv("DEMO", raising=False)
        monkeypatch.delenv("TG_API_ID", raising=False)
        monkeypatch.delenv("TG_API_HASH", raising=False)
        monkeypatch.setattr("app.accounts.TelegramClient", FakeClient)
        monkeypatch.setattr("app.accounts.StringSession", FakeStringSession)
        import app.main
        return importlib.reload(app.main).app
    return build


def sign_in(c, phone, code="12345", password=None):
    r = c.post("/api/tg/start", json={"api_id": "123", "api_hash": "abc", "phone": phone})
    assert r.status_code == 200, r.text
    r = c.post("/api/tg/code", json={"code": code})
    if r.status_code == 200 and r.json()["step"] == "password":
        r = c.post("/api/tg/password", json={"password": password})
    return r


def test_two_people_stay_separate_and_survive_restart(make_app):
    app = make_app()
    with TestClient(app) as a, TestClient(app) as b:
        assert a.get("/api/auth").json()["signed_in"] is False
        assert a.get("/api/channels").status_code == 401
        assert sign_in(a, "+919876543210").status_code == 200
        assert sign_in(b, "+14155550142").status_code == 200
        assert a.get("/api/channels").json()[0]["title"].endswith("+919876543210")
        assert b.get("/api/channels").json()[0]["title"].endswith("+14155550142")
        a.post("/api/progress", json={"key": "k", "t": 5, "d": 10})
        assert a.get("/api/progress").json()["k"]["t"] == 5
        assert b.get("/api/progress").json() == {}
        cookies = dict(a.cookies)
    app = make_app()  # server restart: same data dir, same secret
    with TestClient(app, cookies=cookies) as a2:
        assert a2.get("/api/auth").json()["signed_in"] is True
        assert a2.get("/api/progress").json()["k"]["t"] == 5


def test_wrong_inputs_give_friendly_errors(make_app):
    with TestClient(make_app()) as c:
        r = c.post("/api/tg/start", json={"api_id": "x", "api_hash": "abc", "phone": "+91"})
        assert r.status_code == 400 and "number" in r.json()["detail"]
        r = c.post("/api/tg/start", json={"api_id": "1", "api_hash": "abc", "phone": "+000111"})
        assert r.status_code == 400 and "phone number" in r.json()["detail"]
        c.post("/api/tg/start", json={"api_id": "1", "api_hash": "abc", "phone": "+911111111111"})
        r = c.post("/api/tg/code", json={"code": "00000"})
        assert r.status_code == 400 and "Wrong code" in r.json()["detail"]
        assert c.post("/api/tg/code", json={"code": "12345"}).status_code == 200


def test_two_step_verification(make_app):
    with TestClient(make_app()) as c:
        c.post("/api/tg/start", json={"api_id": "1", "api_hash": "abc", "phone": "+912fa"})
        assert c.post("/api/tg/code", json={"code": "12345"}).json()["step"] == "password"
        assert c.post("/api/tg/password", json={"password": "nope"}).status_code == 400
        assert c.post("/api/tg/password", json={"password": "secret"}).json()["step"] == "done"
        assert c.get("/api/auth").json()["signed_in"] is True


def test_invite_password_gate(make_app, monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda s: _noop())
    with TestClient(make_app("friends")) as c:
        assert c.get("/api/auth").json()["gate_ok"] is False
        assert c.post("/api/tg/start", json={"phone": "+911"}).status_code == 401
        assert c.post("/api/login", json={"password": "bad"}).status_code == 401
        assert c.post("/api/login", json={"password": "friends"}).status_code == 200
        assert c.get("/api/auth").json()["gate_ok"] is True


async def _noop():
    return None


def test_delete_account_revokes_and_wipes(make_app, tmp_path):
    with TestClient(make_app()) as c:
        sign_in(c, "+919999999999")
        assert any((tmp_path / "users").iterdir())
        assert c.post("/api/account/delete").status_code == 200
        assert "+919999999999" in FakeClient.logged_out
        assert not any(p.is_dir() for p in (tmp_path / "users").iterdir())
        assert c.get("/api/auth").json()["signed_in"] is False


def test_channel_selection_is_saved_per_person(make_app):
    app = make_app()
    with TestClient(app) as a, TestClient(app) as b:
        sign_in(a, "+911111111111"); sign_in(b, "+922222222222")
        assert a.get("/api/prefs").json() == {"channels": []}
        a.post("/api/prefs", json={"channels": [-100123, -100456]})
        assert a.get("/api/prefs").json() == {"channels": [-100123, -100456]}
        assert b.get("/api/prefs").json() == {"channels": []}


def test_browser_can_restore_login_after_server_loses_its_disk(make_app, tmp_path):
    """Simulates a free Render restart: new empty data dir, browser sends back what it saved."""
    with TestClient(make_app("friends", tmp_path / "before")) as c:
        c.post("/api/login", json={"password": "friends"})
        sign_in(c, "+919876543210")
        saved = c.get("/api/tg/session").json()
        assert saved["session"] == "sess-+919876543210" and saved["uid"]
        cookies = dict(c.cookies)

    with TestClient(make_app("friends", tmp_path / "after"), cookies=cookies) as c2:
        auth = c2.get("/api/auth").json()
        assert auth["gate_ok"] is True        # password cookie survives the restart
        assert auth["signed_in"] is False     # but the server forgot the Telegram login
        r = c2.post("/api/tg/restore", json={
            **saved, "progress": {"k": {"t": 7, "d": 10, "done": False}}, "prefs": {"channels": [-100123]}})
        assert r.status_code == 200, r.text
        assert c2.get("/api/auth").json()["signed_in"] is True
        assert c2.get("/api/progress").json()["k"]["t"] == 7
        assert c2.get("/api/prefs").json() == {"channels": [-100123]}
        assert c2.get("/api/channels").json()[0]["title"].endswith("+919876543210")


def test_restore_rejects_expired_or_garbage_logins(make_app):
    with TestClient(make_app()) as c:
        r = c.post("/api/tg/restore", json={"api_id": "1", "api_hash": "a", "session": ""})
        assert r.status_code == 400 and "expired" in r.json()["detail"]
        r = c.post("/api/tg/restore", json={"api_id": "nope", "api_hash": "a", "session": "x"})
        assert r.status_code == 400 and "invalid" in r.json()["detail"]
        assert c.get("/api/auth").json()["signed_in"] is False
        assert c.get("/api/tg/session").status_code == 401
