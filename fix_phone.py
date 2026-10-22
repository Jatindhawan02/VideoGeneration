"""Run once in the bot folder: python fix_phone.py
Patches phone.py so YouTube opens even when Appium cannot find its launch screen."""
import re, shutil, sys

PATH = "phone.py"
try:
    text = open(PATH, encoding="utf-8").read()
except FileNotFoundError:
    sys.exit("phone.py not found here. Open PowerShell inside the affiliate-bot folder and run again.")

if "monkey" in text:
    sys.exit("phone.py already has the fix. Nothing to do.")

NEW = '''def open_app(d, pkg):
    try:
        d.terminate_app(pkg)
    except Exception:
        pass
    time.sleep(1)
    try:
        d.activate_app(pkg)
    except Exception as e:
        # Appium cannot resolve the app's launch activity (YouTube) -> start it through adb instead
        print("  activate_app failed, opening with adb:", str(e)[:80])
        adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
'''
pattern = re.compile(r"def open_app\(d, pkg\):.*?(?=\n\ndef |\Z)", re.S)
if not pattern.search(text):
    sys.exit("Could not find open_app in phone.py. Please send me your phone.py.")
shutil.copy(PATH, PATH + ".bak")
open(PATH, "w", encoding="utf-8").write(pattern.sub(NEW.rstrip("\n"), text, count=1))
print("Done. phone.py fixed (old copy saved as phone.py.bak). Now run: python main.py")
