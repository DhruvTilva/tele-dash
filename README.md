# Tele Dash

A personal dashboard for course videos stored in Telegram channels. It lists every video in order, plays them in the browser while they stream from Telegram (no full download first, seeking works), remembers where you stopped, and works on mobile.

## Try it first (no Telegram needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEMO=1 uvicorn app.main:app --port 8000     # needs ffmpeg once, to make a sample video
```
Open http://localhost:8000

## Use it with your Telegram

1. Go to https://my.telegram.org -> *API development tools* -> create an app, and copy `api_id` and `api_hash`.
2. `cp .env.example .env` and fill in `TG_API_ID` and `TG_API_HASH`.
3. Log in once (enter phone number, then the code Telegram sends you):
   ```bash
   python login.py
   ```
4. Start the dashboard:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
5. Open http://localhost:8000, pick your channel, click a video.

To open it on your phone while on the same Wi-Fi, use `http://<your-pc-ip>:8000`.

## Features

- Channel list on the left (a drawer on mobile); videos and PDFs in sequence order with clean titles taken from the `Title » ...` caption line
- Streaming playback with seek, speed control, autoplay next, prev/next
- Resume where you left off, watched tick, progress bar per video (saved on the server, so PC and phone stay in sync)
- Search, filter (videos / PDFs / unwatched), sort (oldest, newest, A-Z)
- Sync button fetches only new messages. Keyboard: `j`/`l` -10s/+10s, `n`/`p` next/prev, space play/pause

## Hosting online

It needs a small Python server (not a static site). Options: your own PC, a cheap VPS, or Render/Railway/Fly.

- **Always set `APP_PASSWORD`** when it is reachable from the internet. Anyone who gets in can browse every channel of your Telegram account.
- Run `python login.py --string` locally and put the printed value in `TG_SESSION_STRING` on the host, so you don't need to log in on the server. Treat that string like a password.
- Keep `data/` on a persistent disk to keep watch progress and the item cache.
- Put it behind HTTPS (the platforms above do this for you).

## Notes

- Browsers play MP4/H.264 best. Some `.mkv` files may not play in every browser; use the download option for those.
- `pip install cryptg` makes Telegram downloads faster.
- Use it for content you have the right to watch.
