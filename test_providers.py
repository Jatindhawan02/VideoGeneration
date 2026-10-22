import sys, zlib, struct
import llm

def make_png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

class _Inline:
    mime_type = "image/png"
    data = make_png(64, 64, (255, 0, 0))

class FakeImage:
    inline_data = _Inline()

wanted = [a.lower() for a in sys.argv[1:]] or ["nvidia", "cloudflare", "zai"]
slots = [s for s in llm._slots() if s[0] in wanted]
if not slots:
    print("Koi provider nahi mila. .env check karo.")
    raise SystemExit

for kind, model in slots:
    print("=== " + kind + " (" + model + ") ===")
    try:
        t = llm._call(kind, model, ["Reply with the single word OK"], False)
        print("  text  : WORKING ->", t.strip()[:40])
    except Exception as e:
        print("  text  : FAIL ->", str(e)[:160])
        continue
    try:
        v = llm._call(kind, model, ["What single color is this image? Answer in one word.", FakeImage()], False)
        ok = "red" in v.lower()
        print("  vision:", "WORKING" if ok else "ajeeb jawab", "->", v.strip()[:40])
    except Exception as e:
        print("  vision: FAIL ->", str(e)[:160])
