"""phone_focus.py - gives the phone back to you whenever the bot does not need it.

What happens during one run (you started the bot from the Chrome of the SAME phone):
  1. the bot opens Meesho and takes the product screenshots       -> the phone is busy
  2. screenshots done -> the bot brings your browser back to the front, so you see the control page and the output
     and can use the phone freely while the script is written       -> PHONE FREE
  3. a few seconds before YouTube Create is needed, the page shows a countdown (PHONE_WARN_SECONDS, default 20)
  4. YouTube Create makes the video                                 -> the phone is busy, please don't touch it
  5. video ready -> your browser comes back again (and again after the Instagram post, if you use that mode)

The control page (index.html) shows a banner for every one of these states. The bot tells the page with lines that start
with "PHONE>> " (server.js reads them).

HOW TO USE: add these two lines to main_link.py, at the very bottom, just ABOVE   if __name__ == "__main__":
    import phone_focus
    phone_focus.install(globals())
It wraps open_link, capture_pages, create_video and post_to_instagram (the ones that exist) and does the rest by itself.

Optional .env / settings:
  PHONE_WARN_SECONDS=20      countdown before the bot takes the phone back (0 = no warning)
  RETURN_TO_BROWSER=1        set by server.js: 1 when you started from the phone, 0 when you started from the PC
  BROWSER_PKG=com.android.chrome   used only when the bot cannot tell which browser you were in
"""
import atexit, os, re, time
import phone

WARN = int(os.getenv("PHONE_WARN_SECONDS", "20"))
RETURN = os.getenv("RETURN_TO_BROWSER", "1").strip().lower() not in ("0", "false", "no", "off")
BROWSER_PKG = os.getenv("BROWSER_PKG", "com.android.chrome")
MODE = os.getenv("MODE", "").strip().lower()

_S = {"comp": None, "state": None, "remembered": False}
# apps that are never "your browser" (what the bot itself uses, the home screen, the keyboard ...)
_NOT_BROWSER = ("meesho", "youtube.producer", "appium", "systemui", "launcher", "instagram", "inputmethod", "settings")


def _say(kind, *parts):
    text = "|".join([kind] + [str(x).replace("|", "/").replace("\n", " ").strip() for x in parts])
    print("PHONE>> " + text, flush=True)


def _foreground():
    """'package/Activity' of the app that is on the screen right now, or None."""
    for cmd, pat in ((("shell", "dumpsys", "activity", "activities"), r"(?:topResumedActivity|ResumedActivity)[^\n]*?\s([\w.]+/[\w.$]+)"),
                     (("shell", "dumpsys", "window"), r"mCurrentFocus=[^\n]*?\s([\w.]+/[\w.$]+)")):
        try:
            m = re.search(pat, phone.adb(*cmd) or "")
        except Exception:
            continue
        if m:
            return m.group(1)
    return None


def remember():
    """Call BEFORE the bot opens the shopping app: notes which app is on the screen now (your browser)."""
    if _S["remembered"]:
        return
    _S["remembered"] = True
    comp = _foreground()
    if comp and not any(w in comp.lower() for w in _NOT_BROWSER):
        _S["comp"] = comp
        print("  [phone] your browser is %s -> it comes back whenever the bot does not need the phone" % comp.split("/")[0])
    else:
        _S["comp"] = None
        print("  [phone] could not tell which app you were in (%s) -> %s will be used" % (comp, BROWSER_PKG))


def _bring_back():
    if not RETURN:
        return
    comp = _S["comp"]
    try:
        if comp:
            out = phone.adb("shell", "am", "start", "-n", comp, "--activity-reorder-to-front") or ""
            if "Error" not in out and "Exception" not in out:
                return
        pkg = comp.split("/")[0] if comp else BROWSER_PKG
        phone.adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    except Exception as e:
        print("  [phone] could not bring the browser back:", str(e)[:100])


def free(msg=""):
    """The bot does not need the phone for a while: show your browser again and tell the page."""
    time.sleep(1)
    _bring_back()
    _S["state"] = "free"
    _say("free", msg)


def need_phone(msg=""):
    """The bot needs the phone: if it was free, warn first (countdown on the page), then mark it busy."""
    if (_S["state"] == "free" or (_S["state"] is None and RETURN)) and WARN > 0:     # None = the page was read on the PC, the phone was never touched yet
        _say("soon", WARN, msg)
        print("  [phone] the bot needs the phone in %d seconds - finish what you are doing" % WARN)
        time.sleep(WARN)
    _S["state"] = "busy"
    _say("busy", msg)


def _final():
    """The program ended while the phone was still busy (an error, say): show your browser again."""
    if _S["state"] == "busy":
        _bring_back()
        _say("free", "The bot has stopped. The phone is yours again.")
        _S["state"] = "free"


def install(ns):
    """ns = globals() of main_link.py. Wraps the phone steps; names that do not exist are skipped."""
    done = []

    def wrap(name, make):
        fn = ns.get(name)
        if callable(fn):
            ns[name] = make(fn)
            done.append(name)

    def w_open(fn):
        def run(*a, **k):
            remember()
            return fn(*a, **k)
        return run

    def w_capture(fn):
        def run(*a, **k):
            r = fn(*a, **k)
            free("Product photos are taken. The script is being written now.")
            return r
        return run

    def w_create(fn):
        def run(*a, **k):
            remember()                  # no-op when the phone was already used for the product page
            need_phone("YouTube Create is making the video.")
            r = fn(*a, **k)             # an error here leaves the phone busy: the next try starts right away
            free("The video is ready." + (" The bot needs the phone again for the Instagram post." if MODE in ("post", "yt_instagram") else ""))
            return r
        return run

    def w_post(fn):
        def run(*a, **k):
            need_phone("Posting on Instagram.")
            try:
                return fn(*a, **k)
            finally:
                free("Posting is finished.")
        return run

    wrap("open_link", w_open)
    wrap("capture_pages", w_capture)
    wrap("create_video", w_create)
    wrap("post_to_instagram", w_post)
    atexit.register(_final)
    print("  [phone] phone-free mode on for: " + (", ".join(done) or "nothing (no matching function names in main_link.py)"))
    if "capture_pages" not in done or "create_video" not in done:
        print("  [phone] WARNING: capture_pages or create_video was not found in main_link.py, the phone will not be handed back.")
