import os, requests
from dotenv import load_dotenv
load_dotenv()
key = (os.getenv("NVIDIA_API_KEY") or "").strip()
r = requests.get("https://integrate.api.nvidia.com/v1/models",
                 headers={"Authorization": "Bearer " + key}, timeout=60)
print("status:", r.status_code)
ids = sorted(m["id"] for m in r.json().get("data", []))
for i in ids:
    low = i.lower()
    if any(w in low for w in ("vl", "vision", "llama-3.2", "phi-3.5-vision", "phi-4-multimodal", "gemma-3", "pixtral", "qwen")):
        print(i)
