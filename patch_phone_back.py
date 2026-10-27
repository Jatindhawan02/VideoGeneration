import glob, os

JOBS = [
    ("server.js", [
        ("const needPhone = p.mode !== 'prompt';", "const needPhone = true;"),
        ("...(needPhone ? {} : { SCRAPE: 'web' }),", "SCRAPE: 'phone',"),
    ]),
    ("main_link.py", [
        ('SCRAPE = (os.getenv("SCRAPE", "") or "auto").strip().lower()',
         'SCRAPE = (os.getenv("SCRAPE", "") or "phone").strip().lower()'),
    ]),
]
files = []
for name, pairs in JOBS:
    hits = [p for p in glob.glob(os.path.join(r"C:\affiliate-bot", "**", name), recursive=True)
            if "node_modules" not in p]
    for p in hits:
        files.append((p, pairs))
if not files:
    raise SystemExit("server.js / main_link.py nahi mili")
for path, pairs in files:
    src = open(path, encoding="utf-8-sig").read()
    if not all(src.count(o) == 1 for o, n in pairs):
        print("SKIP (anchor nahi mila / pehle se badla hua):", path)
        continue
    out = src
    for o, n in pairs:
        out = out.replace(o, n, 1)
    open(path + ".bak_phone", "w", encoding="utf-8").write(src)
    open(path, "w", encoding="utf-8", newline="").write(out)
    print("patched:", path)
