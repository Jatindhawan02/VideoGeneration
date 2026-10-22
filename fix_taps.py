"""Run once in the bot folder: python fix_taps.py
Makes taps use the real screenshot size, so taps near the bottom (like YouTube's + button) land correctly."""
import shutil, sys

PATH = "phone.py"
try:
    text = open(PATH, encoding="utf-8").read()
except FileNotFoundError:
    sys.exit("phone.py not found here. Open PowerShell inside the affiliate-bot folder and run again.")

if "_real_size" in text:
    sys.exit("phone.py already has this fix. Nothing to do.")

OLD = "    return webdriver.Remote(APPIUM_URL, options=opts)"
NEW = '''    d = webdriver.Remote(APPIUM_URL, options=opts)
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
    return d'''
HELPER = '''def _real_size(d):
    import io
    from PIL import Image
    w, h = Image.open(io.BytesIO(d.get_screenshot_as_png())).size
    return {"width": w, "height": h}

'''
if OLD not in text:
    sys.exit("Could not find the place to patch in phone.py. Please send me your phone.py.")
if "def connect():" not in text:
    sys.exit("Could not find connect() in phone.py. Please send me your phone.py.")
shutil.copy(PATH, PATH + ".bak2")
text = text.replace("def connect():", HELPER + "def connect():", 1).replace(OLD, NEW, 1)
open(PATH, "w", encoding="utf-8").write(text)
print("Done. Taps now use the real screenshot size (old copy saved as phone.py.bak2). Now run main_link.py again.")
