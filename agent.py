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
- Reply done only when the goal is visibly complete.
- After you type, the on-screen keyboard may disappear: that is normal. Do NOT type the same text again; look at the box and, if the text is there, continue with the next part of the goal.
- Do NOT tap the bottom navigation bar (Home / Explore / Create / Library tabs) unless the goal explicitly says so.
- Never reply fail just because a button or text field is not visible yet: first wait, swipe, or tap options like Create/Continue/Next to explore. fail is only for login/OTP/CAPTCHA/payment/account warnings or when truly stuck after many tries."""

FORBIDDEN = []      # words in a button label that must never be tapped (set temporarily by supervisor.assist)
MEM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def shrink(png, width=540):
    """Make the screenshot small (fast, light on free APIs) - tap coordinates are normalized so this is safe."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im = im.resize((width, int(im.height * width / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


VALID = {"tap": ("x", "y"), "type": ("text",), "swipe": ("direction",),
         "back": (), "wait": (), "done": (), "fail": ()}


def _normalize(a):
    """The AI sometimes answers numbers as text ('190' or 190.0): turn them into ints."""
    for k in ("x", "y", "seconds"):
        if k in a:
            try:
                a[k] = int(round(float(str(a[k]).strip())))
            except Exception:
                pass
    return a


def _parse_action(text):
    return _normalize(_parse_raw(text))


def _parse_raw(text):
    """Turn the AI answer into a dict. Tries normal JSON, then json-repair if installed."""
    text = (text or "").strip()
    try:
        r = json.loads(text)
        if isinstance(r, dict):
            return r
    except Exception:
        pass
    try:
        import json_repair          # pip install json-repair
        r = json_repair.loads(text)
        if isinstance(r, dict):
            return r
    except Exception:
        pass
    raise ValueError("not valid JSON")


def _has_text_field(d):
    """True if an editable text box exists on the current screen (Appium, free, no AI)."""
    try:
        return bool(d.find_elements("class name", "android.widget.EditText"))
    except Exception:
        return True      # cannot check -> do not block


def _can_type(history):
    """A text field must have been tapped, and not already typed into, since the last 'type'."""
    for h in reversed(history):
        a = h.get("action")
        if a == "tap":
            return True
        if a == "type":
            return False
    return False


def _check(a, history):
    """Return an error string if the AI action makes no sense, else None."""
    act = a.get("action")
    if act not in VALID:
        return f"unknown action {act!r}"
    for k in VALID[act]:
        if k not in a:
            return f"missing '{k}'"
    if act == "tap":
        try:
            if not (0 <= float(a["x"]) <= 1000 and 0 <= float(a["y"]) <= 1000):
                return "x/y must be between 0 and 1000"
        except Exception:
            return "x/y must be numbers"
    if act == "swipe" and a.get("direction") not in ("up", "down", "left", "right"):
        return "swipe direction must be up/down/left/right"
    if act == "type" and not _can_type(history):
        return "tap a text field first (and do not type twice in the same field)"
    if act in ("type", "swipe", "wait") and len(history) >= 2 and \
       all(h.get("action") == act and h.get("text") == a.get("text") and h.get("direction") == a.get("direction")
           for h in history[-2:]):
        return f"you already did '{act}' twice in a row, try something different"
    return None


def decide(png, goal, history, notes=""):
    lessons = f"\n\nLESSONS FROM EARLIER RUNS (trust these, do not rediscover them):\n{notes}" if notes else ""
    prompt = f"{RULES}{lessons}\n\nGOAL: {goal}\n\nRecent actions: {json.dumps(history[-6:])}"

    def validate(text):
        try:
            return _check(_parse_action(text), history)
        except Exception as e:
            return str(e)

    text = llm.generate([types.Part.from_bytes(data=shrink(png), mime_type="image/jpeg"), prompt],
                        validate=validate)
    return _parse_action(text)


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


def _norm(t):
    return re.sub(r"\s+", " ", t or "").strip().lower()


def _find_button(nodes, label, W, H, avoid_bottom=True):
    """Pure helper: return (cx, cy) in pixels of the button called `label`, or None.
    Handles labels wrapped over two lines ('Generate\nvideo') and labels split into two text nodes."""
    label = _norm(label)
    limit = 0.92 * H if avoid_bottom else H + 1

    def ctr(n):
        return (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2

    # pass 1: one element whose text/description contains the whole label (whitespace-insensitive)
    hits = [n for n in nodes
            if label in _norm(n["text"]) or label in _norm(n["desc"]) or label in _norm(n["text"] + " " + n["desc"])]
    hits = [n for n in hits if ctr(n)[1] <= limit]
    if hits:
        return ctr(min(hits, key=_area))

    # pass 2: label split over two elements, e.g. "Generate" above "video"
    words = label.split(" ")
    if len(words) >= 2:
        first, rest = words[0], " ".join(words[1:])
        A = [n for n in nodes if _norm(n["text"] or n["desc"]) == first]
        B = [n for n in nodes if _norm(n["text"] or n["desc"]) == rest]
        for na in A:
            ax, ay = ctr(na)
            for nb in B:
                bx, by = ctr(nb)
                if abs(ax - bx) < 0.15 * W and 0 <= by - ay < 0.08 * H and (ay + by) / 2 <= limit:
                    return (ax + bx) / 2, (ay + by) / 2
    return None


def tap_text(d, labels, timeout=20, avoid_bottom=True):
    """Find a button by its text / description (Appium, no AI guessing) and tap its centre.
    labels: list of texts to look for. Returns True if tapped. Never taps the bottom nav bar."""
    end = time.time() + timeout
    nodes = []
    while True:
        try:
            W, H = _screen(d)
            nodes = _ui_nodes(d)
            for label in labels:
                hit = _find_button(nodes, label, W, H, avoid_bottom)
                if hit:
                    cx, cy = hit
                    print(f"  tap_text: found '{label}' at ({int(cx)},{int(cy)}) -> tapping")
                    phone.tap_norm(d, int(round(cx * 1000 / W)), int(round(cy * 1000 / H)))
                    time.sleep(2)
                    return True
        except Exception as e:
            print("  tap_text error:", str(e)[:80])
        if time.time() >= end:
            break
        time.sleep(2)
    print(f"  tap_text: {labels} not found on screen. Labels Appium can see (send me this):")
    seen = set()
    for n in nodes:
        for v in (n["text"], n["desc"]):
            v = _norm(v)
            if v and v not in seen and len(seen) < 40:
                seen.add(v)
                print("     -", repr(v), n["b"])
    return False


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
            _type_text(d, texts[slot])
        else:
            _type_text(d, st.get("text", ""))
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
def _run(d, goal, max_steps, key, texts, notes, trace, block_bottom=False):
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
            a = decide(png, goal, history, notes)
        except Exception as e:
            errs += 1
            print(f"  AI ERROR ({errs}): {e}")
            if errs >= 4:
                return False, f"AI keeps failing: {str(e)[:200]}"
            time.sleep(8)
            continue
        errs = 0
        history.append(a)
        if a.get("action") not in ("done", "fail"):
            trace.append(a)
        print(f"  step {i+1}: {a}")
        act = a.get("action")
        if act == "type" and not _has_text_field(d):
            # the AI wants to type but there is no text field: do NOT type, tell it to find the field first
            print("  (type rejected: no text field visible on screen)")
            history[-1] = {"action": "type_rejected",
                           "why": "no text field is on screen; explore the screen (tap options like Create/Continue, swipe) and tap the prompt box first"}
            if trace:
                trace.pop()
            time.sleep(3)
            continue
        if act == "type" and texts and typed >= len(texts) and \
           _norm(str(a.get("text", "")))[:40] in [_norm(t)[:40] for t in texts]:
            print("  (type rejected: that text was already typed)")
            history[-1] = {"action": "type_rejected",
                           "why": "that text is already typed in the box; the keyboard closing is normal. Look at the screen and continue with the next part of the goal"}
            if trace:
                trace.pop()
            continue
        if act == "tap" and block_bottom and float(a["y"]) > 915:
            # bottom navigation bar (Home/Explore/+/Projects/You): never allowed for this task
            print("  (tap rejected: that is the bottom navigation bar)")
            history[-1] = {"action": "tap_rejected",
                           "why": "that is the bottom navigation bar, not allowed; use the buttons in the main area of the screen"}
            if trace:
                trace.pop()
            continue
        if act == "tap" and FORBIDDEN:
            loc = _loc_for_tap(d, a["x"], a["y"]) or {}
            lab = (str(loc.get("text", "")) + " " + str(loc.get("desc", ""))).lower()
            if any(w in lab for w in FORBIDDEN):
                print("  (tap rejected: '" + lab.strip()[:40] + "' is not allowed here)")
                history[-1] = {"action": "tap_rejected",
                               "why": "that button is not allowed (upload/share/delete/etc). find another way"}
                if trace:
                    trace.pop()
                continue
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
            res = _type_text(d, text)
            if res is False and typed > 0:
                typed -= 1          # nothing appeared in the box: allow one more try with the exact text
                print("  (text did not appear in the box; tap the box again and retry)")
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


# ------------------------------------------------------------- typing ----
_ime_reset_done = False


def _adb_input_text(text):
    """Type plain ASCII text with 'adb shell input text' (real key presses through the normal keyboard).
    Unlike Appium's 'mobile: type' it does NOT switch the keyboard (IME), so the keyboard stays open."""
    text = text.encode("ascii", "ignore").decode().replace("%", "").replace("\\", "")
    lines = text.replace("\r", "").split("\n")
    for li, line in enumerate(lines):
        for i in range(0, len(line), 40):
            chunk = line[i:i + 40]
            if not chunk:
                continue
            enc = chunk.replace(" ", "%s").replace("'", "'\\''")
            phone.adb("shell", "input text '" + enc + "'")
            time.sleep(0.15)
        if li < len(lines) - 1:
            phone.adb("shell", "input keyevent 66")         # Enter = new line
            time.sleep(0.15)


def _verify_typed(d, text):
    """True = the text is visible in a text box, False = text boxes exist but it is not there, None = cannot tell."""
    probe = _norm(text)[:12]
    try:
        els = d.find_elements("class name", "android.widget.EditText")
        if not els:
            return None
        for e in els:
            v = _norm(e.text or e.get_attribute("text") or "")
            if probe and probe in v:
                return True
        return False
    except Exception:
        return None


def _type_text(d, text):
    """Type into the focused field. Returns True (verified), False (verified missing) or None (unknown).
    Order: adb input text (keeps keyboard open) -> Appium mobile:type -> send_keys -> shell fallback."""
    global _ime_reset_done
    if not _ime_reset_done:
        _ime_reset_done = True
        try:
            phone.adb("shell", "ime reset")        # undo any earlier switch to Appium's hidden keyboard
            time.sleep(1)
        except Exception:
            pass
    ascii_part = text.encode("ascii", "ignore").decode()
    if len(ascii_part) >= 0.85 * max(1, len(text)):       # mostly plain letters: use real key presses
        try:
            _adb_input_text(text)
            time.sleep(1)
            ok = _verify_typed(d, ascii_part)
            if ok is not False:
                if len(ascii_part) < len(text):
                    print("  (note: emojis/non-English letters could not be typed this way and were skipped)")
                return ok
            print("  typed via adb but text not visible in the box")
        except Exception as e:
            print("  adb input text failed:", str(e)[:80])
    try:
        d.execute_script("mobile: type", {"text": text})
        return _verify_typed(d, text)
    except Exception as e:
        print("  mobile:type failed:", str(e)[:80])
    try:
        d.switch_to.active_element.send_keys(text)
        return _verify_typed(d, text)
    except Exception as e:
        print("  send_keys failed:", str(e)[:80])
    safe = re.sub(r"[^A-Za-z0-9 .,!?_@#:/-]", "", text).replace(" ", "%s")
    d.execute_script("mobile: shell", {"command": "input", "args": ["text", safe]})
    return _verify_typed(d, text)


# ------------------------------------------------------------- lessons ----
# Short plain-text tips the AI writes for itself after a run (notes/<task>.txt).
# They are shown to the AI on the next run, so it does not rediscover the same things.
# You can open/edit/delete these files any time. playbook.md = YOUR own rules, added to every call.
NOTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes")
PLAYBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playbook.md")


def _notes_key(goal, key):
    if key:
        return key
    words = re.sub(r"[^a-z0-9 ]", "", goal.lower()).split()[:6]
    return "auto_" + "_".join(words)[:40] + "_" + hashlib.md5(" ".join(words).encode()).hexdigest()[:6]


def _read(path, limit):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()[:limit]
    except Exception:
        return ""


def _load_notes(nk):
    parts = [_read(PLAYBOOK, 1500), _read(os.path.join(NOTES_DIR, nk + ".txt"), 1500)]
    return "\n".join(p for p in parts if p)


def _learn(nk, goal, trace, ok, why):
    """One small AI call: update the lessons file from what happened in this run."""
    if not ok:
        return          # learn ONLY from successful runs (failed runs wrote "give up" rules that made things worse)
    old = _read(os.path.join(NOTES_DIR, nk + ".txt"), 1500)
    actions = "\n".join(f"- {a.get('action')} {a.get('text', '') or a.get('direction', '')}: {str(a.get('why', ''))[:90]}"
                        for a in trace[-40:])
    prompt = (
        "You keep short lessons for a bot that controls an Android phone (Meesho, YouTube, Instagram).\n"
        f"GOAL: {goal[:300]}\nOUTCOME: {'success' if ok else 'failed'} ({str(why)[:150]})\n"
        f"ACTIONS THIS RUN (with the reason given at the time):\n{actions}\n\n"
        f"EXISTING LESSONS:\n{old or '(none)'}\n\n"
        "Write the UPDATED lessons: at most 8 short lines, each one a concrete reusable tip "
        "(which screen or button leads where, which approach works best, a mistake to avoid). "
        "Keep old lessons that are still right, drop wrong ones, add only what this run clearly showed. "
        "If nothing new was learned, repeat the existing lessons. Plain text lines only, no numbering, no intro."
    )
    text = llm.generate([prompt], json_mode=False, rounds=1).strip()
    lines = [l.strip(" -*\t") for l in text.splitlines() if l.strip(" -*\t")][:8]
    if not lines:
        return
    os.makedirs(NOTES_DIR, exist_ok=True)
    with open(os.path.join(NOTES_DIR, nk + ".txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines)[:1500])
    print(f"  lessons updated: notes/{nk}.txt")


def run_task(d, goal, max_steps=40, key=None, texts=None, block_bottom=False):
    """key: name for this task (enables exact-step replay). texts: texts to type, in order (e.g. [prompt]).
    Lessons (what worked / what to avoid) are remembered for every task, with or without a key."""
    nk = _notes_key(goal, key)
    notes = _load_notes(nk)
    if notes:
        print(f"  using lessons from earlier runs ({nk})")
    trace = []
    ok, why = _run(d, goal, max_steps, key, list(texts or []), notes, trace, block_bottom)
    ai_steps = len(trace)
    if (ok and ai_steps >= 3) or (not ok and ai_steps >= 2):    # skip when replay did all the work
        try:
            _learn(nk, goal, trace, ok, why)
        except Exception as e:
            print("  (could not update lessons:", str(e)[:80], ")")
    return ok, why
