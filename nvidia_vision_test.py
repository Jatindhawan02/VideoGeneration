import os, requests, zlib, struct, base64
from dotenv import load_dotenv
load_dotenv()

def make_png(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

b64 = base64.b64encode(make_png(64, 64, (255, 0, 0))).decode()
key = (os.getenv("NVIDIA_API_KEY") or "").strip()
models = [
    "meta/llama-3.2-90b-vision-instruct",
    "meta/llama-3.2-11b-vision-instruct",
    "google/gemma-3-12b-it",
    "microsoft/phi-3-vision-128k-instruct",
]
for m in models:
    print("=== " + m + " ===")
    try:
        r = requests.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={"model": m, "max_tokens": 50, "messages": [{"role": "user", "content": [
                {"type": "text", "text": "What single color is this image? Answer in one word."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]},
            timeout=90,
        )
    except Exception as e:
        print("  FAIL (network):", str(e)[:150])
        continue
    if r.status_code == 200:
        try:
            print("  WORKING. Reply:", (r.json()["choices"][0]["message"].get("content") or "").strip()[:60])
        except Exception:
            print("  200 aaya par format ajeeb:", r.text[:150])
    else:
        print("  FAIL", r.status_code, r.text[:160])
