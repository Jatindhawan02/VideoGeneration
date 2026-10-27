import subprocess, time
from appium import webdriver
from appium.options.android import UiAutomator2Options
from config import SERIAL, APPIUM_URL

def adb(*args):
    cmd = ["adb"] + (["-s", SERIAL] if SERIAL else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()

def _real_size(d):
    import io
    from PIL import Image
    w, h = Image.open(io.BytesIO(d.get_screenshot_as_png())).size
    return {"width": w, "height": h}

def connect():
    if SERIAL and ":" in SERIAL:
        subprocess.run(["adb", "connect", SERIAL], capture_output=True, text=True)
    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.automation_name = "UiAutomator2"
    opts.no_reset = True
    opts.new_command_timeout = 3600
    if SERIAL:
        opts.udid = SERIAL
    d = webdriver.Remote(APPIUM_URL, options=opts)
    d._heal_opts = opts
    # The AI points at the SCREENSHOT, so taps must be converted with the screenshot's real size.
    try:
        real = _real_size(d)
        win = d.get_window_size()
        if (real["width"], real["height"]) != (win["width"], win["height"]):
            print(f"  note: screenshot is {real['width']}x{real['height']} but window size says "
                  f"{win['width']}x{win['height']} -> using the screenshot size for taps")
        d.get_window_size = lambda: dict(real)
    except Exception as e:
        print("  could not read real screen size:", str(e)[:80])
    return d

def screenshot(d):
    return d.get_screenshot_as_png()

def _alive(d):
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
        d.activate_app(pkg)
    except Exception as e:
        print("  activate_app failed, opening with adb:", str(e)[:80])
        import subprocess, os
        serial = os.getenv("ANDROID_SERIAL", "")
        cmd = ["adb"] + (["-s", serial] if serial else []) + [
            "shell", "monkey", "-p", pkg,
            "-c", "android.intent.category.LAUNCHER", "1"]
        subprocess.run(cmd, check=False)
        import time
        time.sleep(3)

def tap_norm(d, x, y):
    """x,y are 0-1000 normalized."""
    size = d.get_window_size()
    px, py = int(x / 1000 * size["width"]), int(y / 1000 * size["height"])
    d.execute_script("mobile: clickGesture", {"x": px, "y": py})

def swipe(d, direction):
    size = d.get_window_size()
    d.execute_script("mobile: swipeGesture", {
        "left": int(size["width"] * 0.1), "top": int(size["height"] * 0.25),
        "width": int(size["width"] * 0.8), "height": int(size["height"] * 0.5),
        "direction": direction, "percent": 0.75})

def type_text(d, text):
    # clipboard paste handles emojis / Hindi
    d.set_clipboard_text(text)
    d.press_keycode(279)  # KEYCODE_PASTE

def push_media(local_path, remote_path):
    adb("push", local_path, remote_path)
    adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
        "-d", f"file://{remote_path}")

def newest_file(folder, ext):
    out = adb("shell", f"ls -t {folder}/*.{ext} 2>/dev/null | head -1")
    return out.strip() or None
