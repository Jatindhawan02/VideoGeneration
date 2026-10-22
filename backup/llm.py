"""One place to call an AI model.

Providers are tried in the order given by PROVIDER_ORDER in .env
(default: gemini,groq,openrouter,mistral). When one hits a limit or is busy it
"rests" for a while and the next provider is used automatically.
generate() works exactly like before, so verify.py / caption.py / find_product.py
need no change.
"""
import base64, os, re, time
import requests
from dotenv import load_dotenv

load_dotenv()
from config import GEMINI_API_KEY, GEMINI_MODEL

try:
    from google import genai
    from google.genai import types
    _gem = (genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=60000))
            if GEMINI_API_KEY else None)
except Exception as _e:          # google-genai missing: Gemini is skipped, others still work
    print("  (Gemini library not available:", str(_e)[:80], ")")
    types = None
    _gem = None

ORDER = [p.strip().lower() for p in os.getenv("PROVIDER_ORDER", "gemini,groq,openrouter,mistral").split(",") if p.strip()]
GEMINI_FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]

COMPAT_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
}
GAP = {"gemini": float(os.getenv("GEMINI_MIN_GAP", "13"))}      # free Gemini = ~5 requests/min
OTHER_GAP = float(os.getenv("OTHER_MIN_GAP", "3"))

_busy = {}      # "provider:model" -> time until which it rests
_last = {}      # "provider:model" -> time of last call
_dead = set()   # wrong key / wrong model name: skipped for this run


class _Http(Exception):
    def __init__(self, code, text, retry=None):
        super().__init__(f"{code} {text[:300]}")
        self.code, self.retry = code, retry


def _slots():
    out = []
    for p in ORDER:
        if p == "gemini":
            if _gem:
                for m in dict.fromkeys([GEMINI_MODEL] + GEMINI_FALLBACKS):
                    if m:
                        out.append(("gemini", m))
        elif p in COMPAT_URLS:
            if os.getenv(p.upper() + "_API_KEY") and os.getenv(p.upper() + "_MODEL"):
                out.append((p, os.getenv(p.upper() + "_MODEL")))
    return out


def _to_openai(contents, json_mode):
    parts, skipped = [], False
    for c in contents:
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
            continue
        inline = getattr(c, "inline_data", None)
        mime = getattr(inline, "mime_type", "") or ""
        data = getattr(inline, "data", None)
        if mime.startswith("image/") and data:
            b64 = base64.b64encode(data).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        else:
            skipped = True      # audio/video cannot be sent to these providers
    if skipped:
        parts.append({"type": "text", "text": "(Audio/video could not be attached. Judge only what you can see and say that the audio was not checked.)"})
    if json_mode:
        parts.append({"type": "text", "text": "Reply with a single valid JSON object only. No markdown, no extra text."})
    return parts


def _clean(text, json_mode):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    if json_mode and not text.lstrip().startswith("["):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
    return text


def _gemini_call(model, contents, json_mode):
    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    text = _gem.models.generate_content(model=model, contents=contents, config=cfg).text
    if not text:
        raise RuntimeError("empty answer from Gemini")
    return text


def _compat_call(kind, model, contents, json_mode):
    key = os.getenv(kind.upper() + "_API_KEY")
    r = requests.post(
        COMPAT_URLS[kind] + "/chat/completions",
        headers={"Authorization": "Bearer " + key},
        json={"model": model, "messages": [{"role": "user", "content": _to_openai(contents, json_mode)}]},
        timeout=90,
    )
    if r.status_code != 200:
        raise _Http(r.status_code, r.text, r.headers.get("retry-after"))
    text = _clean(r.json()["choices"][0]["message"].get("content") or "", json_mode)
    if not text:
        raise RuntimeError(f"empty answer from {kind}")
    return text


def _call(kind, model, contents, json_mode):
    if kind == "gemini":
        return _gemini_call(model, contents, json_mode)
    return _compat_call(kind, model, contents, json_mode)


def _parse_wait(e):
    ra = getattr(e, "retry", None)
    try:
        if ra:
            return float(ra)
    except Exception:
        pass
    low = str(e).lower()
    m = re.search(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:(\d+(?:\.\d+)?)s)?", low)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    m = re.search(r"retrydelay\W+(\d+(?:\.\d+)?)s", low)
    if m:
        return float(m.group(1))
    return None


def _rest(k, e):
    """Decide what to do with a provider after an error."""
    msg = str(e)
    low = msg.lower()
    code = getattr(e, "code", None)
    if code is None:
        m = re.search(r"\b([45]\d\d)\b", msg)
        code = int(m.group(1)) if m else 0
    print(f"  [{k}] problem: {msg[:140]}")
    if code in (401, 403) or "api key not valid" in low or "permission_denied" in low:
        print(f"  [{k}] key looks wrong -> skipping it for this run")
        _dead.add(k)
        return
    if code == 404 or "not_found" in low or "decommissioned" in low or "does not exist" in low:
        print(f"  [{k}] model name looks wrong -> skipping it for this run")
        _dead.add(k)
        return
    if code == 429 or "resource_exhausted" in low or "rate limit" in low or "quota" in low:
        daily = any(w in low for w in ("perday", "per day", "per_day", "daily"))
        wait = _parse_wait(e) or (10800 if daily else 60)
    elif code >= 500 or "timed out" in low or "timeout" in low or "unavailable" in low or "overloaded" in low:
        wait = 45
    else:
        wait = 20
    wait = max(wait, 20)
    _busy[k] = time.time() + wait
    print(f"  [{k}] resting {int(wait)}s, trying the next provider")


def generate(contents, json_mode=True, rounds=4):
    if isinstance(contents, str):
        contents = [contents]
    slots = _slots()
    if not slots:
        raise RuntimeError("No AI key found. Put GEMINI_API_KEY / GROQ_API_KEY (+ *_MODEL) in .env")
    last = None
    for r in range(rounds):
        for kind, model in slots:
            k = f"{kind}:{model}"
            if k in _dead or _busy.get(k, 0) > time.time():
                continue
            wait = GAP.get(kind, OTHER_GAP) - (time.time() - _last.get(k, 0))
            if wait > 0:
                time.sleep(wait)
            _last[k] = time.time()
            try:
                return _call(kind, model, contents, json_mode)
            except Exception as e:
                last = e
                _rest(k, e)
        pending = [_busy.get(f"{a}:{b}", 0) for a, b in slots if f"{a}:{b}" not in _dead]
        if not pending:
            break
        soonest = min(pending) - time.time()
        if soonest > 120:
            break
        if soonest > 0:
            print(f"  all providers busy, waiting {int(soonest) + 1}s...")
            time.sleep(soonest + 1)
    raise last or RuntimeError("All AI providers are resting (limit reached). Try again later.")
