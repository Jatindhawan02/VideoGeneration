src = open("yt_flow.py", encoding="utf-8-sig").read()
OLD = """    def ok(want, have):
        if want is None:
            return True
        return have.strip().lower() == want.lower() if exact else want.lower() in have.lower()
"""
NEW = """    def ok(want, have):
        if want is None:
            return True
        h = " ".join(have.split()).lower()
        w = " ".join(want.split()).lower()
        if exact:
            return h == w or h == w + " " + w
        return w in h
"""
if OLD not in src:
    raise SystemExit("Patch ki jagah nahi mili, kuch nahi badla")
open("yt_flow.py.bak", "w", encoding="utf-8").write(src)
open("yt_flow.py", "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("yt_flow.py patched")
