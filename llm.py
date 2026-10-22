"""One place to call an AI model.

Providers are tried in the order given by PROVIDER_ORDER in .env
(default: gemini,groq,nvidia,cloudflare,zai,openrouter,mistral).
When one hits a limit or is busy it "rests" for a while and the next provider
is used automatically. Rest times are saved in llm_state.json so a restart of
the bot does not waste quota on providers that are still resting.

Gemini can use several API keys (GEMINI_API_KEY + GEMINI_API_KEYS=k2,k3,k4).
Each key is its own slot ("gemini", "gemini2", "gemini3", ...) with its own rest
timer and request gap. For each model, all keys are tried before moving on.

generate(..., validate=fn): fn(text) returns an error string to reject an answer
(that provider rests 5 min and the next one is tried), or None if it is fine.
"""
import base64, json, os, re, time
import requests
from dotenv import load_dotenv

load_dotenv()
from config import GEMINI_API_KEY, GEMINI_MODEL

try:
    from google import genai
    from google.genai import types
except Exception as _e:          # google-genai missing: Gemini is skipped, others still work
    print("  (Gemini library not available:", str(_e)[:80], ")")
    genai = None
    types = None


def _gemini_keys():
    raw = [GEMINI_API_KEY] + os.getenv("GEMINI_API_KEYS", "").split(",")
    keys = []
    for k in raw:
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


_gems = {}      # "gemini", "gemini2", "gemini3", ... -> client (one per API key)
if genai:
    for _i, _k in enumerate(_gemini_keys(), 1):
        _gems["gemini" if _i == 1 else f"gemini{_i}"] = genai.Client(
            api_key=_k, http_options=types.HttpOptions(timeout=60000))
    if _gems:
        print(f"  (Gemini: {len(_gems)} API key(s) loaded)")

ORDER = [p.strip().lower() for p in os.getenv("PROVIDER_ORDER", "gemini,groq,nvidia,cloudflare,zai,openrouter,mistral").split(",") if p.strip()]
GEMINI_FALLBACKS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]

COMPAT_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "zai": "https://api.z.ai/api/paas/v4",
}
KEY_ENV = {"cloudflare": "CLOUDFLARE_API_TOKEN"}     # all others use <NAME>_API_KEY
GEMINI_GAP = float(os.getenv("GEMINI_MIN_GAP", "13"))   # per key; free Gemini = ~5 requests/min
OTHER_GAP = float(os.getenv("OTHER_MIN_GAP", "3"))
MAX_WAIT = float(os.getenv("MAX_WAIT_SECONDS", "120"))          # longer than this -> stop instead of waiting

# Max images one request may contain (providers reject more). Change in .env, e.g. GROQ_MAX_IMAGES=3
MAX_IMAGES = {"groq": 3, "nvidia": 1, "mistral": 8, "openrouter": 8, "zai": 5, "cloudflare": 5}
for _k in list(MAX_IMAGES):
    try:
        MAX_IMAGES[_k] = int(os.getenv(_k.upper() + "_MAX_IMAGES", MAX_IMAGES[_k]))
    except ValueError:
        pass

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_state.json")

_busy = {}      # "provider:model" -> time until which it rests
_last = {}      # "provider:model" -> time of last call
_dead = set()   # wrong key / wrong model name: skipped for this run


class AllBusy(RuntimeError):
    """Every provider is resting. Callers should stop, not retry in a loop."""


class _Http(Exception):
    def __init__(self, code, text, retry=None):
        super().__init__(f"{code} {text[:300]}")
        self.code, self.retry = code, retry


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        for k, v in data.items():
            if float(v) > now:
                _busy[k] = float(v)
    except Exception:
        pass


def _save_state():
    try:
        now = time.time()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in _busy.items() if v > now}, f)
    except Exception:
        pass


_load_state()


def _env(name):
    return (os.getenv(name) or "").strip()


def _key(kind):
    return _env(KEY_ENV.get(kind, kind.upper() + "_API_KEY"))


def _base_url(kind):
    if kind == "cloudflare":
        acc = _env("CLOUDFLARE_ACCOUNT_ID")
        return ("https://api.cloudflare.com/client/v4/accounts/" + acc + "/ai/v1") if acc else ""
    return COMPAT_URLS.get(kind, "")


def _slots():
    out = []
    for p in ORDER:
        if p == "gemini":
            for m in dict.fromkeys([GEMINI_MODEL] + GEMINI_FALLBACKS):
                if m:
                    for kind in _gems:          # same model on every key first
                        out.append((kind, m))
        elif p in COMPAT_URLS or p == "cloudflare":
            if _key(p) and _env(p.upper() + "_MODEL") and _base_url(p):
                out.append((p, _env(p.upper() + "_MODEL")))
    return out


def _to_openai(contents, json_mode, max_images=None):
    parts, skipped, n_img = [], False, 0
    for c in contents:
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
            continue
        inline = getattr(c, "inline_data", None)
        mime = getattr(inline, "mime_type", "") or ""
        data = getattr(inline, "data", None)
        if mime.startswith("image/") and data:
            if max_images is not None and n_img >= max_images:
                continue        # this provider accepts only max_images pictures per request: keep the first ones
            n_img += 1
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


def _gemini_call(kind, model, contents, json_mode):
    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    text = _gems[kind].models.generate_content(model=model, contents=contents, config=cfg).text
    if not text:
        raise RuntimeError(f"empty answer from {kind}")
    return text


def _compat_call(kind, model, contents, json_mode):
    r = requests.post(
        _base_url(kind) + "/chat/completions",
        headers={"Authorization": "Bearer " + _key(kind)},
        json={"model": model, "messages": [{"role": "user", "content": _to_openai(contents, json_mode, MAX_IMAGES.get(kind))}]},
        timeout=90,
    )
    if r.status_code != 200:
        raise _Http(r.status_code, r.text, r.headers.get("retry-after"))
    text = _clean(r.json()["choices"][0]["message"].get("content") or "", json_mode)
    if not text:
        raise RuntimeError(f"empty answer from {kind}")
    return text


def _call(kind, model, contents, json_mode):
    if kind in _gems:
        return _gemini_call(kind, model, contents, json_mode)
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
    if code in (404, 410) or "not_found" in low or "decommissioned" in low or "does not exist" in low or "end of life" in low:
        print(f"  [{k}] model name looks wrong or retired -> skipping it for this run")
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
    _save_state()
    print(f"  [{k}] resting {int(wait)}s, trying the next provider")


def generate(contents, json_mode=True, rounds=4, validate=None):
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
            wait = (GEMINI_GAP if kind in _gems else OTHER_GAP) - (time.time() - _last.get(k, 0))
            if wait > 0:
                time.sleep(wait)
            _last[k] = time.time()
            try:
                out = _call(kind, model, contents, json_mode)
            except Exception as e:
                last = e
                _rest(k, e)
                continue
            if validate:
                try:
                    err = validate(out)
                except Exception as ve:
                    err = f"validator crashed: {ve}"
                if err:
                    print(f"  [{k}] bad answer ({err}) -> next provider")
                    _busy[k] = time.time() + 300      # confused/weak model: rest 5 min
                    _save_state()
                    last = RuntimeError(f"bad answer: {err}")
                    continue
            print(f"  [{k}] ok")
            return out
        live = [f"{a}:{b}" for a, b in slots if f"{a}:{b}" not in _dead]
        if not live:
            break
        soonest = min(_busy.get(k, 0) for k in live) - time.time()
        if soonest > MAX_WAIT:
            break
        if soonest > 0:
            print(f"  all providers busy, waiting {int(soonest) + 1}s...")
            time.sleep(soonest + 1)
    live = [f"{a}:{b}" for a, b in slots if f"{a}:{b}" not in _dead]
    if not live:
        raise last or RuntimeError("No usable AI provider (all keys/models look wrong). Check .env")
    mins = max(1, int((min(_busy.get(k, 0) for k in live) - time.time()) / 60) + 1)
    raise AllBusy(f"All AI providers are resting. Earliest one is free in about {mins} min. STOP and run again later. Last error: {str(last)[:150]}")
