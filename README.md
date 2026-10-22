# Affiliate Reel Bot (one video/day)

## One-time setup
1. `pip install -r requirements.txt`
2. Install FFmpeg and make sure `ffmpeg` and `adb` work in a terminal.
3. Copy `.env.example` to `.env` and fill in GEMINI_API_KEY, ANDROID_SERIAL (from `adb devices`), and optionally Telegram.
4. Edit `products.csv`: one row per affiliate product (name, description, status=pending).
5. Start Appium (`appium`) and make sure `adb devices` shows the phone as `device`.
6. Keep the phone unlocked-capable: screen timeout long, "Stay awake" on, no screen lock PIN (or the bot cannot wake it).

## Test
`python main.py`  - watch the phone. Failures send a Telegram message and save a screenshot in logs/.

## Schedule (Windows)
Task Scheduler -> Create Basic Task -> Daily 10:00 -> Start a program -> run_daily.bat
(Appium must also start at boot; add a second task: `appium` at logon.)
Linux/Mac cron: `0 10 * * * cd /path/affiliate-bot && python main.py >> logs/run.log 2>&1`

## Notes
- The bot refuses to continue at login/OTP/CAPTCHA screens and alerts you instead.
- Wi-Fi ADB can drop; USB is more reliable for unattended runs.
- Automating apps may violate their terms. Use an account you can afford to lose.
