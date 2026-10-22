"""Vision agent: screenshot -> AI decides next action -> Appium performs it.

LEARN ONCE, REPLAY: when run_task() gets a `key`, a successful run is saved in
memory/<key>.json. Next time those steps are replayed WITHOUT the AI: each tap
finds its button again by text / id (free, instant). The AI is only called when
a button is not found (screen changed) or to double-check the goal at the end.
Delete the memory folder to make the bot learn everything again.
"""
import io, json, os, re, time, hashlib
import xml.etree.ElementTree as ET
from PIL import Image
from google.genai import types
import llm
import phone

RULES = """You are controlling an Android phone by looking at a screenshot.
Reply with ONE JSON object only, choosing one action:
{"action":"tap","x":0-1000,"y":0-1000,"why":"..."}   (coordinates normalized to 0-1000, top-left origin)
{"action":"type","text":"...","why":"..."}           (types into the currently focused field; tap the field first)
{"action":"swipe","direction":"up|down|left|right","why":"..."}
{"action":"back","why":"..."}
{"action":"wait","seconds":5,"why":"..."}
{"action":"done","why":"goal fully achieved"}
{"action":"fail","why":"cannot continue"}
Rules:
- Take the smallest sensible next step toward the goal.
- If you see a login screen, OTP, CAPTCHA, password prompt, 'verify it's you', payment, or an account warning: reply fail.
- Dismiss harmless popups (notifications, rate us, tips) with their close/not now button.
- Never buy anything, never change account settings, never delete content.
- Reply done only when the goal is visibly complete."""

MEM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def shrink(png, width=540):
    """Make the screenshot small (fast, light on free APIs) - tap coordinates are normalized so this is safe."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im = im.resize((width, int(im.height * width / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def decide(png, goal, history):
    prompt = f"{RULES}\n\nGOAL: {goal}\n\nRecent actions: {json.dumps(history[-6:])}"
    text = llm.generate([types.Part.from_bytes(data=shrink(png), mime_type="image/jpeg"), prompt])
    return json.loads(text)


# ---------------------------------------------------------------- memory ----
def _load(key):
    try:
        with open(os.path.join(MEM_DIR, key + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save(key, steps):
    try:
        os.makedirs(MEM_DIR, exist_ok=True)
        with open(os.path.join(MEM_DIR, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(steps, f, ensure_ascii=False, indent=1)
        print(f"  learned: saved {len(steps)} steps as '{key}' (next run needs little or no AI)")
    except Exception as e:
        print("  could not save memory:", e)


def _area(n):
    x1, y1, x2, y2 = n["b"]
    return (x2 - x1) * (y2 - y1)


def _ui_nodes(d):
    """All on-screen elements (text, id, description, position) from Appium - free, no AI."""
    try:
        root = ET.fromstring(d.page_source)
    except Exception:
        return []
    out = []
    for n in root.iter():
        m = _BOUNDS.match(n.attrib.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({"b": (x1, y1, x2, y2), "id": n.attrib.get("resource-id", ""),
                    "text": n.attrib.get("text", ""), "desc": n.attrib.get("content-desc", "")})
    return out


def _screen(d):
    s = d.get_window_size()
    return s["width"], s["height"]


def _label_at(nodes, px, py, W, H):
    """Describe the element at this point by its text/id/description (None if it has no label)."""
    cands = [n for n in nodes if n["b"][0] <= px <= n["b"][2] and n["b"][1] <= py <= n["b"][3]]
    if not cands:
        return None
    cands.sort(key=_area)
    smallest = _area(cands[0])
    for n in cands:
        if _area(n) > 0.25 * W * H or _area(n) > 8 * max(smallest, 1):
            break
        loc = {k: n[k] for k in ("id", "text", "desc") if n[k]}
        if loc:
            return loc
    return None


def _find_node(nodes, loc, near):
    """Find the element with this label; if several, the one closest to where it was before."""
    best, best_d = None, None
    for n in nodes:
        if all(n[k] == v for k, v in loc.items()):
            cx, cy = (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2
            dd = (cx - near[0]) ** 2 + (cy - near[1]) ** 2
            if best_d is None or dd < best_d:
                best, best_d = (cx, cy), dd
    return best


def _loc_for_tap(d, x, y):
    try:
        W, H = _screen(d)
        return _label_at(_ui_nodes(d), x * W / 1000, y * H / 1000, W, H)
    except Exception:
        return None


def _replay_step(d, st, texts):
    """Do one saved step without the AI. Returns False if the screen is not as expected."""
    act = st.get("action")
    if act == "tap":
        loc = st.get("loc")
        if loc:
            W, H = _screen(d)
            hit = None
            for _ in range(3):                      # screen may still be loading
                hit = _find_node(_ui_nodes(d), loc, (st["x"] * W / 1000, st["y"] * H / 1000))
                if hit:
                    break
                time.sleep(2)
            if not hit:
                return False
            phone.tap_norm(d, int(round(hit[0] * 1000 / W)), int(round(hit[1] * 1000 / H)))
        else:
            phone.tap_norm(d, st["x"], st["y"])      # no label known: same spot as last time
    elif act == "type":
        slot = st.get("slot")
        if slot is not None:
            if slot >= len(texts):
                return False
            phone.type_text(d, texts[slot])
        else:
            phone.type_text(d, st.get("text", ""))
    elif act == "swipe":
        phone.swipe(d, st["direction"])
    elif act == "back":
        d.back()
    elif act == "wait":
        time.sleep(min(int(st.get("seconds", 5)), 60))
    else:
        return False
    time.sleep(2)
    return True


# ------------------------------------------------------------------ main ----
def run_task(d, goal, max_steps=40, key=None, texts=None):
    """key: name for this task (enables learn/replay). texts: texts to type, in order (e.g. [prompt])."""
    texts = list(texts or [])
    history, executed = [], []
    last_hash, same, errs, typed = None, 0, 0, 0

    # 1) replay what was learned before (no AI)
    saved = _load(key) if key else None
    if saved:
        print(f"  replaying {len(saved)} saved steps for '{key}' (no AI)")
        for st in saved:
            if not _replay_step(d, st, texts):
                print("  screen differs from last time -> AI takes over from here")
                break
            executed.append(st)
            history.append({"action": st.get("action"), "why": "replayed"})
            if st.get("action") == "type":
                typed += 1

    # 2) AI loop (also confirms the goal is done after a replay)
    for i in range(max_steps):
        png = phone.screenshot(d)
        h = hashlib.md5(png).hexdigest()
        same = same + 1 if h == last_hash else 0
        last_hash = h
        if same >= 4:
            return False, "screen not changing (stuck)"
        try:
            a = decide(png, goal, history)
        except Exception as e:
            errs += 1
            print(f"  AI ERROR ({errs}): {e}")
            if errs >= 4:
                return False, f"AI keeps failing: {str(e)[:200]}"
            time.sleep(8)
            continue
        errs = 0
        history.append(a)
        print(f"  step {i+1}: {a}")
        act = a.get("action")
        if act == "done":
            if key and executed:
                _save(key, executed)
            return True, a.get("why", "")
        if act == "fail":
            return False, a.get("why", "")
        rec = None
        if act == "tap":
            rec = {"action": "tap", "x": a["x"], "y": a["y"]}
            if key:
                rec["loc"] = _loc_for_tap(d, a["x"], a["y"])
            phone.tap_norm(d, a["x"], a["y"])
        elif act == "type":
            if typed < len(texts):                  # use the exact text we were given
                text, rec = texts[typed], {"action": "type", "slot": typed}
            else:
                text, rec = a["text"], {"action": "type", "text": a["text"]}
            typed += 1
            phone.type_text(d, text)
        elif act == "swipe":
            rec = {"action": "swipe", "direction": a["direction"]}
            phone.swipe(d, a["direction"])
        elif act == "back":
            rec = {"action": "back"}
            d.back()
        elif act == "wait":
            secs = min(int(a.get("seconds", 5)), 60)
            rec = {"action": "wait", "seconds": secs}
            time.sleep(secs)
        if rec:
            executed.append(rec)
        time.sleep(2)
    return False, "max steps reached"
