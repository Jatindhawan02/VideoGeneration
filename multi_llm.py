import os, time, base64, requests
from dotenv import load_dotenv

load_dotenv()

# name: (base_url, ENV prefix)
PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL"),
}

ORDER = [p.strip() for p in os.getenv("PROVIDER_ORDER", "gemini,groq,openrouter,mistral").split(",") if p.strip()]
busy_until = {}   # provider -> time jab tak aaram
dead = set()      # galat key/model wale, is run mein skip


def _call(name, prompt, image_path):
    base, prefix = PROVIDERS[name]
    key = os.getenv(prefix + "_API_KEY", "")
    model = os.getenv(prefix + "_MODEL", "")
    content = [{"type": "text", "text": prompt}]
    if image_path:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}})
    r = requests.post(
        base + "/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        timeout=90,
    )
    return r


def ask(prompt, image_path=None):
    """Text ya (text + screenshot) bhejo, jawab text mein milega."""
    for attempt in range(6):
        tried = False
        for name in ORDER:
            if name not in PROVIDERS or name in dead:
                continue
            prefix = PROVIDERS[name][1]
            if not os.getenv(prefix + "_API_KEY") or not os.getenv(prefix + "_MODEL"):
                continue
            if busy_until.get(name, 0) > time.time():
                continue
            tried = True
            try:
                r = _call(name, prompt, image_path)
            except Exception as e:
                print(f"[{name}] network error: {e} -> 30s aaram")
                busy_until[name] = time.time() + 30
                continue
            if r.status_code == 200:
                try:
                    return r.json()["choices"][0]["message"]["content"]
                except Exception:
                    busy_until[name] = time.time() + 30
                    continue
            if r.status_code in (429, 500, 502, 503, 504):
                wait = 60
                try:
                    wait = int(float(r.headers.get("retry-after", 60)))
                except Exception:
                    pass
                if "day" in r.text.lower():   # daily limit khatam
                    wait = 3 * 3600
                busy_until[name] = time.time() + max(wait, 20)
                print(f"[{name}] limit/busy ({r.status_code}) -> {max(wait,20)}s aaram, agla provider try")
                continue
            # 400/401/403/404: key ya model naam galat
            print(f"[{name}] ERROR {r.status_code}: {r.text[:200]} -> is run mein skip")
            dead.add(name)
        if not tried:
            waits = [t for n, t in busy_until.items() if n not in dead and t > time.time()]
            if not waits:
                break
            sleep_for = min(waits) - time.time()
            if sleep_for > 180:
                break
            print(f"Sab busy hain, {int(sleep_for)+1}s ruk raha hoon...")
            time.sleep(sleep_for + 1)
    raise RuntimeError("Koi bhi provider jawab nahi de paya (sab limit/error).")


if __name__ == "__main__":
    print(ask("Sirf ek word bolo: hello"))