"""One-time Telegram login. Run:  python login.py   (add --string to also print a session string for hosting)"""
import os
import sys

from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

load_dotenv()
api_id, api_hash = os.getenv("TG_API_ID"), os.getenv("TG_API_HASH")
if not api_id or not api_hash:
    sys.exit("Fill TG_API_ID and TG_API_HASH in .env first (get them at https://my.telegram.org).")

data_dir = os.getenv("DATA_DIR", "data")
os.makedirs(data_dir, exist_ok=True)

with TelegramClient(os.path.join(data_dir, "tele-dash"), int(api_id), int(api_hash)) as client:
    me = client.get_me()
    print(f"Logged in as {me.first_name} (@{me.username}). Session saved in {data_dir}/")
    client.get_dialogs()  # remember your channels
    if "--string" in sys.argv:
        print("\nTG_SESSION_STRING=" + StringSession.save(client.session))
        print("(keep this secret - it gives full access to your Telegram account)")
