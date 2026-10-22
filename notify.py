import requests
from config import TG_TOKEN, TG_CHAT

def send(msg):
    print("[notify]", msg)
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": msg}, timeout=20)
    except Exception as e:
        print("telegram failed:", e)
