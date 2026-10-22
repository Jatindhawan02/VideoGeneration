def rd(p):
    return open(p, encoding="utf-8-sig").read()

ML = [
(r"""        prompt = script["yt_prompt"]""",
 r"""        prompt = script["yt_prompt"]
        print("  YouTube prompt (%d chars): %s" % (len(prompt), prompt))
        with open(os.path.join(out_dir, "yt_prompt.txt"), "w", encoding="utf-8-sig") as _f:
            _f.write(prompt)"""),
(r"""            phone.push_media(shot_local, shot_remote)""",
 r"""            for _i, (_pth, _png) in reversed(list(enumerate(carousel[1:3], 2))):
                try:
                    _im2 = Image.open(_pth)
                    _w2, _h2 = _im2.size
                    _p2 = os.path.join(out_dir, "reference_%d.png" % _i)
                    _im2.crop((0, int(.08 * _h2), _w2, int(.55 * _h2))).save(_p2)
                    _r2 = "/sdcard/Pictures/product_shot_%d_%d.png" % (int(time.time()), _i)
                    phone.push_media(_p2, _r2)
                    phone.adb("shell", "touch", _r2)
                    time.sleep(1.5)
                except Exception as _e:
                    print("  (extra photo skipped:", str(_e)[:80], ")")
            phone.push_media(shot_local, shot_remote)"""),
]
YT = [
(r"""             "an element whose description says 'selected'")""",
 r"""             "an element whose description says 'selected'")
    # optional: add up to 2 more product photos (never fails the run)
    try:
        for idx in (1, 2):
            before = len(find(d, desc="selected"))
            t = [n for n in find(d, rid="mediaThumbnail-%d" % idx) if n["id"].endswith("mediaThumbnail-%d" % idx)]
            if not t:
                break
            if "selected" in t[0]["desc"].lower():
                continue
            tap(d, t[0])
            time.sleep(2)
            after = len(find(d, desc="selected"))
            print("  extra photo %d: selected count %d -> %d" % (idx, before, after))
            if after <= before:
                t0 = [n for n in find(d, rid="mediaThumbnail-0") if n["id"].endswith("mediaThumbnail-0")]
                if t0 and "selected" not in t0[0]["desc"].lower():
                    tap(d, t0[0])
                    time.sleep(2)
                break
    except Exception as e:
        print("  (extra photos skipped:", str(e)[:80], ")")"""),
]
jobs = [("main_link.py", ML), ("yt_flow.py", YT)]
data = {}
for path, pairs in jobs:
    src = rd(path)
    for o, n in pairs:
        if src.count(o) != 1:
            raise SystemExit(path + ": patch ki jagah nahi mili (" + o[:40].strip() + "), kuch nahi badla")
    data[path] = src
for path, pairs in jobs:
    src = data[path]
    out = src
    for o, n in pairs:
        out = out.replace(o, n, 1)
    open(path + ".bak5", "w", encoding="utf-8").write(src)
    open(path, "w", encoding="utf-8").write(out)
    print(path, "patched")
