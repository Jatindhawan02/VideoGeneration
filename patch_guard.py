"""Run once from the project folder:  python patch_guard.py
Hooks supervisor.guard() into yt_flow.wait_for (needs patch_popup.py to have been applied already)."""
src = open("yt_flow.py", encoding="utf-8-sig").read()

OLD1 = "_DRV = None\n"
NEW1 = "import supervisor\n_DRV = None\n"
OLD2 = "        if _DRV is not None:\n            dismiss_popups(_DRV)\n        time.sleep(every)\n    return None\n"
NEW2 = ("        if _DRV is not None:\n            dismiss_popups(_DRV)\n            supervisor.guard(_DRV)\n"
        "        time.sleep(every)\n    return None\n")

for o in (OLD1, OLD2):
    if src.count(o) != 1:
        raise SystemExit("Patch ki jagah nahi mili, kuch nahi badla")
out = src.replace(OLD1, NEW1, 1).replace(OLD2, NEW2, 1)
open("yt_flow.py.bak4", "w", encoding="utf-8").write(src)
open("yt_flow.py", "w", encoding="utf-8").write(out)
print("yt_flow.py patched")
