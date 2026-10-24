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

1. Start the dashboard:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. Open http://localhost:8000. The first screen asks you to connect Telegram:
   - Get your **API ID** and **API hash** at https://my.telegram.org -> *API development tools* -> create an app (any name).
   - Enter them with your phone number, then the login code Telegram sends to your Telegram app (and your 2-step password if you have one).
3. Pick your channel and click a video. That's it - no `.env` file needed.

To open it on your phone while on the same Wi-Fi, use `http://<your-pc-ip>:8000`.

## Sharing with friends

Everyone who opens the site signs in with **their own** Telegram account on the same setup screen. Each person gets a private space on the server (their own session, channel list, and watch progress), so friends never see your channels or each other's.

- Set `APP_PASSWORD` as an invite password so only people you give it to can reach the site.
- Optional: put your own `TG_API_ID` / `TG_API_HASH` in `.env` and friends only need a phone number (they can still choose to use their own keys).
- **Settings** (bottom of the sidebar) lets anyone log out on that device, or disconnect Telegram and delete their data from the server.
- Signing in on a second device (e.g. phone) repeats the same setup there; progress is shared because it's tied to the Telegram account.

## Features

- Choose which channels appear in the sidebar from **Settings -> My channels**
- Channel list on the left (a drawer on mobile); videos and PDFs in sequence order with clean titles taken from the `Title » ...` caption line
- Streaming playback with seek, speed control, autoplay next, prev/next
- PDFs open in a built-in viewer (works on phones too) with a Download button; PDF.js is bundled in `web/vendor`, no internet needed
- Resume where you left off, watched tick, progress bar per video (saved on the server, so PC and phone stay in sync)
- Search, filter (videos / PDFs / unwatched), sort (oldest, newest, A-Z)
- Sync button fetches only new messages. The ☰ button (or `b`) collapses the channel sidebar for a bigger player. Keyboard: `j`/`l` -10s/+10s, `n`/`p` next/prev, space play/pause

## Hosting online

It needs a small Python server (not a static site). Options: your own PC, a cheap VPS, or Render/Railway/Fly.

- **Set `APP_PASSWORD`** when it is reachable from the internet.
- Whoever hosts it stores friends' Telegram login sessions on that server, so only share it with people who trust you, and use HTTPS (the platforms above provide it).
- Keep `data/` on a persistent disk, otherwise everyone must sign in again after a redeploy. Never share or commit it (it holds sessions).

## Staying signed in on hosts that wipe their disk (e.g. free Render)

Tick **Keep me signed in on this device** when connecting (on by default). The browser then keeps your Telegram session plus a copy of your watch progress and channel choices. If the server restarts and forgets everything, the page signs you back in automatically within a few seconds. With `APP_PASSWORD` set, the password cookie also survives restarts.

- **Log out** (Settings) clears the saved login from the browser. Don't tick it on a shared computer: anyone using that browser could open your channels.
- You can end the session any time from Telegram -> Settings -> Devices.
- Only the login, progress and channel choices are restored. The video list and thumbnails are re-fetched from Telegram (a few seconds).

## Notes

- Browsers play MP4/H.264 best. Some `.mkv` files may not play in every browser; use the download option for those.
- **Speed:** videos are fetched from Telegram in 1 MB chunks, 4 at a time in parallel, with a small in-memory cache for seeking back. Raise `STREAM_LANES` in `.env` (e.g. 6 or 8) if your internet is fast; lower it if Telegram rate-limits you.
- `pip install cryptg` also makes Telegram downloads noticeably faster (less CPU spent decrypting). Recommended.
- Use it for content you have the right to watch.
