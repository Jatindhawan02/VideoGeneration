"""user_feedback.py - lets YOU steer the bot from the control page while it runs, and make a video again with a change.

Two kinds of notes (the page sends them to server.js, which writes them to live_feedback.json; this file reads them):

  "Change the video"  - while the bot waits for YouTube Create (or just before the next try): the bot stops waiting for the
                        current video, changes the YouTube prompt the way you wrote, and starts the video again.
                        The spoken words (everything after "Voiceover:") are never changed, so the voice check still works.
                        Your notes stay for the rest of this run (every retry uses them).
  "Help on the phone" - the bot is stuck on some screen: tell it what to do ("tap Allow", "close the rating popup").
                        An AI helper follows it on the phone (it can never publish, share, buy or press Generate).

"Make again with this change" (button on the page, after a run): the bot reuses the product screenshots AND the script of the
last run (saved in run_state.json inside that run's folder), so it does not open Meesho again and does not write a new script.
It applies your note to the YouTube prompt and makes the video again.

HOW TO USE: in main_link.py, at the very bottom just above  main(),  these lines (user_feedback FIRST, then phone_focus):
    import user_feedback, phone_focus
    user_feedback.install(globals())
    phone_focus.install(globals())
Settings come from server.js (FEEDBACK_FILE, REUSE_RUN, USER_FEEDBACK). Nothing to edit here.
"""
import atexit, glob, json, os, re, time

FEEDBACK_FILE = os.getenv("FEEDBACK_FILE", "").strip()          # the page's notes arrive in this file
REUSE_RUN = os.getenv("REUSE_RUN", "").strip()                  # folder of the last run (set by "Make again with this change")
START_NOTE = re.sub(r"\s+", " ", os.getenv("USER_FEEDBACK", "")).strip()[:500]
MAX_PROMPT = int(os.getenv("YT_PROMPT_MAX", "880"))
HINT_MINUTES = 10
STATE_NAME = "run_state.json"
YT_PKG_DEFAULT = os.getenv("YTCREATE_PKG", "com.google.android.apps.youtube.producer")

_S = {"seen": 0, "video_notes": [START_NOTE] if START_NOTE else [], "hints": [], "cache": {}, "applied": 0,
      "interrupt": False, "out_dir": None, "announced": False}


def _clean(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()[:500]


# ------------------------------------------------------------- reading notes ----
def poll():
    """New notes from the page: [{id, kind, text}]. Cheap (one small file), safe to call often."""
    if not FEEDBACK_FILE:
        return []
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except Exception:
        return []
    new = []
    for x in items:
        try:
            i = int(x.get("id", 0))
        except Exception:
            continue
        if i > _S["seen"] and _clean(x.get("text")):
            new.append({"id": i, "kind": "phone" if x.get("kind") == "phone" else "video", "text": _clean(x.get("text"))})
            _S["seen"] = i
    return new


def _take(new):
    """Remember new notes. Returns (video_notes, phone_hints) from this batch."""
    vids, hints = [], []
    for x in new:
        if x["kind"] == "phone":
            _S["hints"].append({"text": x["text"], "at": time.time(), "used": False})
            hints.append(x["text"])
            print("  [note] you told the bot (phone): " + x["text"])
            try:                                   # keep it for the next runs too (memory/mistakes.json)
                import agent as _ag
                if _ag.CURRENT_SCOPE[0]:
                    _ag.record_mistake(_ag.CURRENT_SCOPE[0], "you told the bot: " + x["text"], "The user corrected the bot (may be a one-off): " + x["text"])
            except Exception as e:
                print("  [note] could not save your note to memory:", str(e)[:80])
        else:
            _S["video_notes"].append(x["text"])
            vids.append(x["text"])
            print("  [note] you asked for a change in the video: " + x["text"])
    return vids, hints


# --------------------------------------------------------- changing the prompt ----
def apply_notes(prompt, notes, product=""):
    """The YouTube prompt with your notes applied. The spoken words stay exactly as they are."""
    m = re.search(r"Voiceover:.*$", prompt, re.S)
    spoken = m.group(0).strip() if m else ""
    ask = (
        "You edit a text prompt that is typed into YouTube Create (an AI video generator). The video made from this prompt had a problem. "
        "Apply the creator's notes: change ONLY what the notes need and keep everything else the same.\n"
        "Product: " + (product or "-") + "\nCREATOR'S NOTES (all of them, oldest first):\n- " + "\n- ".join(notes) + "\n\n"
        "CURRENT PROMPT:\n" + prompt + "\n\n"
        "Rules: keep the whole sentence that starts with 'Voiceover:' and EVERYTHING after it exactly word for word (do not change, move or remove it). "
        "Keep the prompt under " + str(MAX_PROMPT) + " characters, plain English, no brand names, no price, no 'best' or 'guaranteed', "
        "no words like baby, kid or child. If a note asks for something the video app cannot do, do the closest safe thing.\n"
        "Return JSON: {\"prompt\": \"the full new prompt\"}")

    def validate(t):
        try:
            new = str(json.loads(t).get("prompt", "")).strip()
        except Exception:
            return "not valid JSON"
        if len(new) < 60:
            return "prompt too short"
        if len(new) > 900:
            return "prompt too long (%d characters, max 900)" % len(new)
        if spoken and spoken not in new:
            return "the sentence starting with 'Voiceover:' and everything after it must stay word for word"
        return None

    try:
        import llm
        return json.loads(llm.generate([ask], validate=validate))["prompt"].strip()
    except Exception as e:
        print("  [note] the AI could not rewrite the prompt (%s) -> adding your note to it directly" % str(e)[:100])
    # fallback: put the note in front of the voice part
    cut = prompt.rfind(" Voice:")
    head, tail = (prompt[:cut], prompt[cut:]) if cut > 0 else (prompt, "")
    room = 899 - len(head) - len(tail) - 2
    note = " ".join(notes)
    if room < 15:
        print("  [note] no room left in the prompt for your note, it was NOT added")
        return prompt
    return (head.rstrip() + " " + note[:room].rstrip() + tail).strip()


def _with_notes(p, prompt):
    notes = list(_S["video_notes"])
    if not notes or not prompt:
        return prompt
    _S["applied"] = len(notes)
    key = (prompt, tuple(notes))
    if key not in _S["cache"]:
        print("  [note] changing the YouTube prompt as you asked: " + " / ".join(notes))
        new = apply_notes(prompt, notes, (p or {}).get("name", ""))
        _S["cache"][key] = new
        if new != prompt:
            print("  new YouTube prompt (%d chars): %s" % (len(new), new))
            print("YTPROMPT>> " + new)             # the control page shows it in its prompt box
    return _S["cache"][key]


# ------------------------------------------------------------ saved run state ----
def _load_state(folder):
    try:
        with open(os.path.join(folder, STATE_NAME), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(folder, **parts):
    if not folder:
        return
    st = _load_state(folder)
    st.update(parts)
    try:
        with open(os.path.join(folder, STATE_NAME), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, default=str)
    except Exception as e:
        print("  [note] could not save run_state.json (Make again will not work for this run):", str(e)[:80])


def _announce(folder):
    if folder and not _S["announced"]:
        _S["announced"] = True
        print("RUNDIR>> " + folder)            # server.js remembers the folder of this run


def _have_replay():
    if not REUSE_RUN:
        return False
    cap = _load_state(REUSE_RUN).get("capture") or {}
    return bool(cap.get("carousel")) and all(os.path.exists(x) for x in cap["carousel"])


def _newest(ns):
    root = ns.get("OUT_ROOT") or ""
    dirs = [x for x in glob.glob(os.path.join(root, "*")) if os.path.isdir(x)]
    return max(dirs, key=os.path.getmtime) if dirs else None


# ---------------------------------------------------------------------- hooks ----
def install(ns):
    """ns = globals() of main_link.py. Hooks into the watcher, the AI phone helper, the screenshots, the script and the video step."""
    done = []
    sup, agent, sw = ns.get("supervisor"), ns.get("agent"), ns.get("script_writer")

    if sup is not None and hasattr(sup, "guard"):
        _guard, FlowProblem = sup.guard, sup.FlowProblem

        def guard(d, every=120):
            vids, hints = _take(poll())
            if vids:
                _S["interrupt"] = True
                print("  [note] stopping the current video try to apply your change...")
                raise FlowProblem("user", "your note: " + " / ".join(vids), "retry")
            for h in hints:
                try:
                    sup.assist(d, "user hint", "the user's instruction is done: " + h, max_steps=8)
                except Exception as e:
                    print("  [note] the helper could not follow your hint:", str(e)[:100])
            return _guard(d, every)
        sup.guard = guard
        done.append("watcher")

    if sup is not None and hasattr(sup, "fix_prompt"):
        _fix = sup.fix_prompt

        def fix_prompt(p, script, old_prompt, problem, attempt):
            if getattr(problem, "kind", "") == "user":
                return old_prompt                      # your note is applied by the video step, not by the 'safer prompt' rewrite
            return _fix(p, script, old_prompt, problem, attempt)
        sup.fix_prompt = fix_prompt
        done.append("fix_prompt")

    if agent is not None and hasattr(agent, "run_task"):
        _run = agent.run_task

        def run_task(d, goal, *a, **k):
            _take(poll())
            live = [h for h in _S["hints"] if not h["used"] and time.time() - h["at"] < HINT_MINUTES * 60]
            if live:
                goal = goal + "\nIMPORTANT, THE USER TELLS YOU: " + " | ".join(h["text"] for h in live) + ". Follow this first when it fits the screen."
                for h in live:
                    h["used"] = True
            return _run(d, goal, *a, **k)
        agent.run_task = run_task

        def _hook():                                   # called by agent.py before AND after every AI step
            _take(poll())
            live = [h for h in _S["hints"] if not h["used"]]
            for h in live:
                h["used"] = True
            return " | ".join(h["text"] for h in live)
        agent.STEP_HOOK = _hook
        done.append("phone helper")

    if callable(ns.get("open_link")):
        _open = ns["open_link"]

        def open_link(d, url):
            if _have_replay():
                print("  [note] Make again: the saved product screenshots are used, Meesho is not opened")
                return True
            return _open(d, url)
        ns["open_link"] = open_link
        done.append("open_link")

    if callable(ns.get("capture_pages")):
        _cap = ns["capture_pages"]

        def capture_pages(d, out_dir, *a, **k):
            _S["out_dir"] = out_dir
            _announce(out_dir)
            if _have_replay():
                cap = _load_state(REUSE_RUN)["capture"]
                rd = lambda paths: [(x, open(x, "rb").read()) for x in paths if os.path.exists(x)]
                if isinstance(ns.get("GALLERY_BOX"), dict):
                    ns["GALLERY_BOX"]["box"] = tuple(cap["box"]) if cap.get("box") else None
                _save_state(out_dir, capture=cap)
                print("  [note] Make again: %d saved product photos re-used" % len(cap["carousel"]))
                return rd(cap["carousel"]), rd(cap.get("info", [])), list(cap.get("texts", []))
            carousel, info, texts = _cap(d, out_dir, *a, **k)
            box = (ns.get("GALLERY_BOX") or {}).get("box")
            _save_state(out_dir, capture={"carousel": [x[0] for x in carousel], "info": [x[0] for x in info], "texts": list(texts),
                                          "box": [float(v) for v in box] if box else None})
            return carousel, info, texts
        ns["capture_pages"] = capture_pages
        done.append("capture_pages")

    if sw is not None and hasattr(sw, "make_script"):
        _make = sw.make_script

        def make_script(p, *a, **k):
            out_dir = _S["out_dir"] or _newest(ns)
            _announce(out_dir)
            if REUSE_RUN:
                saved = _load_state(REUSE_RUN).get("script")
                if saved:
                    print("  [note] Make again: the script of the last run is re-used (no new script is written)")
                    _save_state(out_dir, script=saved)
                    return saved
                print("  [note] no saved script in " + REUSE_RUN + " -> writing a new one")
            sc = _make(p, *a, **k)
            _save_state(out_dir, script=sc)
            return sc
        sw.make_script = make_script
        done.append("make_script")

    if callable(ns.get("create_video")):
        _cv = ns["create_video"]

        def create_video(d, p, shot_remote, prompt=None, n_images=1, *a, **k):
            _take(poll())
            if _S["interrupt"]:
                _S["interrupt"] = False
                try:                                   # the abandoned generation must not block the next try
                    import phone
                    phone.adb("shell", "am", "force-stop", ns.get("YT_PKG") or YT_PKG_DEFAULT)
                    time.sleep(2)
                except Exception as e:
                    print("  [note] could not close YouTube Create:", str(e)[:80])
            return _cv(d, p, shot_remote, _with_notes(p, prompt), n_images, *a, **k)
        ns["create_video"] = create_video
        done.append("create_video")

    def _late():
        _take(poll())
        if len(_S["video_notes"]) > _S["applied"]:
            print("  [note] a change you sent came too late for this run: " + " / ".join(_S["video_notes"][_S["applied"]:]) +
                  "  ->  use 'Make again with this change' on the page")
    atexit.register(_late)
    print("  [note] you can steer the bot from the page now (hooked: " + (", ".join(done) or "nothing") + ")")
    if not {"watcher", "create_video"} <= set(done):
        print("  [note] WARNING: the watcher or create_video was not found, live changes will not work.")
