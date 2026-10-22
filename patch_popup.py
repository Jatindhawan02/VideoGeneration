src = open("yt_flow.py", encoding="utf-8-sig").read()

OLD1 = """def wait_for(fn, seconds=20, every=2):
    end = time.time() + seconds
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(every)
    return None
"""
NEW1 = """_DRV = None
POPUP_NO = ["Not now", "No thanks", "Maybe later", "Later", "Don't allow", "Don\\u2019t allow", "Deny", "Skip"]


def dismiss_popups(d):
    try:
        for lb in POPUP_NO:
            h = find_label(d, lb)
            if h:
                print("  popup mila, '" + lb + "' dabaya")
                tap(d, h[0])
                time.sleep(2)
                return True
    except Exception:
        pass
    return False


def wait_for(fn, seconds=20, every=2):
    end = time.time() + seconds
    while time.time() < end:
        r = fn()
        if r:
            return r
        if _DRV is not None:
            dismiss_popups(_DRV)
        time.sleep(every)
    return None
"""
OLD2 = "def run(d, prompt):\n"
NEW2 = "def run(d, prompt):\n    global _DRV\n    _DRV = d\n"
OLD3 = "        time.sleep(settle)\n        got = None\n"
NEW3 = "        time.sleep(settle)\n        dismiss_popups(d)\n        got = None\n"

for o in (OLD1, OLD2, OLD3):
    if src.count(o) != 1:
        raise SystemExit("Patch ki jagah nahi mili, kuch nahi badla")
out = src.replace(OLD1, NEW1, 1).replace(OLD2, NEW2, 1).replace(OLD3, NEW3, 1)
open("yt_flow.py.bak3", "w", encoding="utf-8").write(src)
open("yt_flow.py", "w", encoding="utf-8").write(out)
print("yt_flow.py patched")
