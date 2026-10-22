src = open("yt_flow.py", encoding="utf-8-sig").read()
OLD = """    export()
    print("  exporting, waiting for a new mp4 on the phone (up to 5 minutes)...")
"""
NEW = """    export()
    wait_for(lambda: find_label(d, "Save to device"), 15, 2)

    def save_dev():
        h = find_label(d, "Save to device")
        if h:
            tap(d, h[0])
    run_step(d, "save to device", save_dev,
             lambda: "sheet closed or saving started" if not find_label(d, "Save to device") else None,
             "the 'Save to device' button to be tapped", retries=2, settle=3)
    print("  exporting, waiting for a new mp4 on the phone (up to 5 minutes)...")
"""
if OLD not in src:
    raise SystemExit("Patch ki jagah nahi mili, kuch nahi badla")
open("yt_flow.py.bak2", "w", encoding="utf-8").write(src)
open("yt_flow.py", "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("yt_flow.py patched")
