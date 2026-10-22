src = open("phone.py", encoding="utf-8-sig").read()

HEAL = r'''def _alive(d):
    try:
        d.current_package
        return True
    except Exception:
        return False


def heal(d):
    """If the Appium session died (idle timeout, crash, reconnect), start a new one on the SAME driver object."""
    if _alive(d):
        return True
    print("  Appium session mar chuka hai -> naya session bana raha hoon...")
    opts = getattr(d, "_heal_opts", None)
    if opts is None:
        return False
    for attempt in (1, 2, 3):
        try:
            if SERIAL and ":" in SERIAL:
                subprocess.run(["adb", "connect", SERIAL], capture_output=True, text=True, timeout=30)
            try:
                d.start_session(opts)
            except Exception:
                d.start_session(opts.to_capabilities())
            if _alive(d):
                print("  naya session OK")
                return True
        except Exception as e:
            print("  session heal try " + str(attempt) + " failed:", str(e)[:100])
        time.sleep(5)
    return False


def open_app(d, pkg):
    if not heal(d):
        raise SystemExit("STOP: Appium se connection nahi ban paa raha. Appium server chalu hai? adb devices mein phone 'device' dikh raha hai? "
                         "Wireless debugging ka port badla ho to .env mein ANDROID_SERIAL update karo.")
    try:
        d.activate_app(pkg)'''

PAIRS = [
    ("def open_app(d, pkg):\n    try:\n        d.activate_app(pkg)", HEAL),
    ("    d = webdriver.Remote(APPIUM_URL, options=opts)\n",
     "    d = webdriver.Remote(APPIUM_URL, options=opts)\n    d._heal_opts = opts\n"),
    ("    opts.new_command_timeout = 600", "    opts.new_command_timeout = 3600"),
]
for o, n in PAIRS:
    if src.count(o) != 1:
        raise SystemExit("Patch ki jagah nahi mili (" + o[:40].strip() + "), kuch nahi badla")
out = src
for o, n in PAIRS:
    out = out.replace(o, n, 1)
open("phone.py.bak9", "w", encoding="utf-8").write(src)
open("phone.py", "w", encoding="utf-8").write(out)
print("phone.py patched")
