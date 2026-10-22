import os, requests
from dotenv import load_dotenv
load_dotenv()

cf_id = (os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()
tests = [
    ("nvidia", "https://integrate.api.nvidia.com/v1/chat/completions",
     os.getenv("NVIDIA_API_KEY"), os.getenv("NVIDIA_MODEL")),
    ("cloudflare", "https://api.cloudflare.com/client/v4/accounts/" + cf_id + "/ai/v1/chat/completions",
     os.getenv("CLOUDFLARE_API_TOKEN"), os.getenv("CLOUDFLARE_MODEL")),
    ("zai", "https://api.z.ai/api/paas/v4/chat/completions",
     os.getenv("ZAI_API_KEY"), os.getenv("ZAI_MODEL")),
]

for name, url, key, model in tests:
    key = (key or "").strip()
    model = (model or "").strip()
    print("=== " + name + " ===")
    if not key or not model or (name == "cloudflare" and not cf_id):
        print("  MISSING: .env mein key/model/account id nahi mili")
        continue
    print("  model:", model)
    try:
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "max_tokens": 200,
                  "messages": [{"role": "user", "content": "Reply with the single word OK"}]},
            timeout=60,
        )
    except Exception as e:
        print("  FAIL (network):", str(e)[:150])
        continue
    if r.status_code == 200:
        try:
            msg = r.json()["choices"][0]["message"]
            text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            print("  WORKING. Reply:", text[:80])
        except Exception:
            print("  200 aaya par format ajeeb:", r.text[:150])
    else:
        print("  FAIL", r.status_code, r.text[:200])
