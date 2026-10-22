import os
from dotenv import load_dotenv
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
SERIAL = os.getenv("ANDROID_SERIAL", "")
APPIUM_URL = os.getenv("APPIUM_URL", "http://127.0.0.1:4723")
SHORT_PROMPT_STYLE = os.getenv("SHORT_PROMPT_STYLE", "")
PKG = {
    "meesho": "com.meesho.supply",
    "youtube": "com.google.android.youtube",
    "instagram": "com.instagram.android",
}
