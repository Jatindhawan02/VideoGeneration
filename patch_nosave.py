src = open("yt_flow.py", encoding="utf-8-sig").read()

OLD1 = r"""    export()
    wait_for(lambda: find_label(d, "Save to device"), 15, 2)
"""
NEW1 = r"""    export()
    wait_for(lambda: find_label(d, "Save to device") or find_label(d, "Upload to YouTube Shorts"), 15, 2)
    if not find_label(d, "Save to device") and (find_label(d, "Upload to YouTube Shorts")
                                                or find(d, text="only be uploaded") or find(d, desc="only be uploaded")):
        print("[FAIL] save to device: YouTube is offering ONLY 'Upload to YouTube Shorts' (no Save to device).")
        dump(d, "no_save_option")
        raise supervisor.FlowProblem("export_blocked", "this account cannot export: generated videos can only be uploaded to YouTube", "give_up")
"""
OLD2 = r"""    except Exception as e:
        print("\nSTOPPED:", e)
"""
NEW2 = r"""    except (Exception, supervisor.FlowProblem) as e:
        print("\nSTOPPED:", e)
"""
for o in (OLD1, OLD2):
    if src.count(o) != 1:
        raise SystemExit("Patch ki jagah nahi mili, kuch nahi badla")
open("yt_flow.py.bak4", "w", encoding="utf-8").write(src)
open("yt_flow.py", "w", encoding="utf-8").write(src.replace(OLD1, NEW1, 1).replace(OLD2, NEW2, 1))
print("yt_flow.py patched")
