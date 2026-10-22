"""supervisor.py - the 'human brain' layer around the fixed YouTube Create steps.

Fixed steps are fast and free. Whenever reality differs from the plan, the AI looks at the screen and acts:
  guard(d)    : called on every poll of every wait. Free text rules first (known errors), AI only when the
                screen changes or 2 minutes pass. AI verdict: ok / error / blocked.
                  error   -> FlowProblem (main_link retries, rewriting the prompt if the app refused it)
                  blocked -> AI clears the dialog right away, then the wait continues
  assist(...) : AI acts like a person to reach what the step expected (accept consent, close popups, go back...).
                Called by yt_flow.run_step when a step's check fails, and by main_link for anything unexpected.
  fix_prompt  : decides what to change before the next try.
Safety: while the AI is driving, taps on upload/share/publish/delete/buy/subscribe/sign-in buttons are refused.
"""
import hashlib, json, os, time
from google.genai import types
import agent, llm, phone

YT_PKG = os.getenv("YTCREATE_PKG", "com.google.android.apps.youtube.producer")
# NOTE: bare "upload" is NOT blocked: the + button that adds the reference photo is labelled
# "Upload up to 3 ingredients" (it only adds a picture, it publishes nothing). Real publishing buttons stay blocked.
FORBIDDEN_TAPS = ["upload to", "upload video", "upload short", "upload now", "upload this", "upload and",
                  "publish", "share", "post", "delete", "remove", "buy", "purchase",
                  "subscribe", "sign in", "log in", "login"]
MAX_GUARD_ASSISTS = int(os.getenv("MAX_GUARD_ASSISTS", "4"))

# Buttons the AI helper may NEVER tap: they leave the 'Generate video' flow (the AI once went to 'Create project').
ALWAYS_FORBID = ["create project", "edit with ai", "generate image", "use template"]
# Only these steps may tap 'Generate' / 'Create'. Every other step must not start a video.
GENERATE_OK = ("open generate video", "start generation", "start")
STEP_HINTS = {
    "open generate video": "tap the 'Generate video' icon in the row of small icons under the big banner on the home screen.",
    "type prompt": "tap the prompt text box once, then type the prompt.",
    "open media picker": "tap ONLY the small + icon next to the prompt box. Its label may say 'Upload up to 3 ingredients': that is CORRECT and SAFE here, it only adds a reference picture and publishes nothing. If a photo or media permission dialog appears, choose Allow.",
    "select image": "tap the first (newest) image thumbnail ONCE in the picker.",
    "confirm image": "tap the picker's confirm button (Add / Done / Next).",
    "start generation": "tap the Generate button on the prompt screen.",
}


class FlowProblem(BaseException):
    """Raised from inside a wait loop. BaseException on purpose: yt_flow's own `except Exception`
    blocks cannot swallow it. main_link catches it explicitly."""

    def __init__(self, kind, reason, fix="retry"):
        super().__init__(kind + ": " + reason)
        self.kind, self.reason, self.fix = kind, reason, fix


REFUSED = ["can't create videos like that", "cant create videos like that", "try something else instead"]
TRANSIENT = ["something went wrong", "couldn't generate", "could not generate", "try again later",
             "no internet", "check your connection", "unable to create"]

_S = {"sig": None, "last_ai": 0.0, "beat": 0.0, "assists": 0, "phase": "starting"}


def reset():
    _S.update(sig=None, last_ai=0.0, beat=0.0, assists=0, phase="starting")


def set_phase(text):
    """Tell the watcher what the bot is doing right now (so the AI judges the screen in context)."""
    _S["phase"] = text
    _S["sig"] = None


def _texts(d):
    out = []
    for n in agent._ui_nodes(d):
        for v in (n["text"], n["desc"]):
            v = (v or "").strip()
            if v:
                out.append(v)
    return out


# ------------------------------------------------------------------ AI eyes ----
def diagnose(d, texts=None):
    """AI looks at the screen. Returns {"state": ok|error|blocked, "reason", "fix"} or None if the AI is unavailable."""
    try:
        png = phone.screenshot(d)
        prompt = (
            "A bot is making an AI video in the YouTube Create app on an Android phone.\n"
            "WHAT THE BOT IS DOING NOW: " + _S["phase"] + "\n"
            "Text on screen: " + " | ".join(texts or [])[:600] + "\n\n"
            "Look at the screenshot and return JSON: {\"state\": \"ok\" | \"error\" | \"blocked\", \"reason\": \"short\", "
            "\"fix\": \"retry\" | \"softer_prompt\" | \"give_up\"}\n"
            "ok      = normal progress for what the bot is doing (prompt box, picker, spinner/progress, editor, export...).\n"
            "error   = an error/refusal/failure message is shown, or work clearly ended without a result.\n"
            "blocked = a dialog, notice, consent/terms/privacy, permission, rating, tip or an unrelated screen is in the way "
            "and needs a tap before the bot can continue.\n"
            "fix (only for error): softer_prompt if the app refused the CONTENT; retry if temporary; "
            "give_up ONLY for login/payment/account problems.")

        def validate(t):
            try:
                if json.loads(t).get("state") not in ("ok", "error", "blocked"):
                    return "state must be ok, error or blocked"
            except Exception:
                return "not valid JSON"
            return None

        v = json.loads(llm.generate([types.Part.from_bytes(data=agent.shrink(png, 540), mime_type="image/jpeg"), prompt],
                                    validate=validate, rounds=1))
        v.setdefault("fix", "retry")
        return v
    except Exception as e:
        print("  [watcher] AI check skipped:", str(e)[:100])
        return None


# ----------------------------------------------------------------- AI hands ----
def assist(d, step, expected, prompt=None, max_steps=10, only_clear=False):
    """The AI acts like a person to reach `expected`. Returns True if it says the goal is reached."""
    goal = (
        "Recover YouTube Create step '" + step + "'. A bot is making an AI-generated video in the YouTube Create app "
        "and expected this: " + expected + ". The screen is not as expected. "
        "Do what a normal person would: accept harmless consent/terms/privacy notices (Continue / Agree / OK), "
        "dismiss tips, rating and permission popups, close a stray sheet or dialog, press Back from a wrong screen, "
        "close whatever hides the screen the step needs. "
        "NEVER publish or upload the finished video to YouTube, and never share, post, delete, buy, subscribe or sign in. "
        "(Adding a reference picture with the + button labelled 'Upload up to 3 ingredients' is allowed and safe.) "
        "Reply fail only for login, payment, CAPTCHA or account warnings. "
        "Done when: " + expected + ".")
    if only_clear:
        goal += (" IMPORTANT: only clear what is blocking the screen. Do NOT press Back and do NOT leave the current screen "
                 "unless it is a wrong screen. If nothing is blocking, reply done immediately.")
    hint = STEP_HINTS.get(step, "")
    if hint:
        goal += " HOW TO DO THIS STEP: " + hint
    goal += (" NEVER tap Create project, Edit with AI, Generate image, Use template or the bottom tabs (Home, Explore, Projects, You)."
             + ("" if step in GENERATE_OK else " Do NOT tap Generate or Create: that would start a video before the bot is ready."))
    old = getattr(agent, "FORBIDDEN", [])
    agent.FORBIDDEN = list(FORBIDDEN_TAPS) + ALWAYS_FORBID + ([] if step in GENERATE_OK else ["generate", "create"])
    try:
        ok, why = agent.run_task(d, goal, max_steps=max_steps,
                                 texts=[prompt] if (prompt and step == "type prompt") else None)
        print("  [watcher] AI helper:", "reached the goal" if ok else "did not reach the goal", "-", str(why)[:100])
        return ok
    except Exception as e:
        print("  [watcher] AI helper error:", str(e)[:120])
        return False
    finally:
        agent.FORBIDDEN = old


def unstick(d, err):
    """Last-chance helper for main_link: get back to the prompt box of 'Generate video'."""
    print("  [watcher] asking the AI to clear the way...")
    return assist(d, "start", "the Generate video screen with its prompt text box visible")


# ------------------------------------------------------------------- watcher ----
def guard(d, every=120):
    """Call on every poll while waiting."""
    try:
        texts = _texts(d)
    except Exception:
        return
    low = " | ".join(texts).lower().replace("\u2019", "'")
    now = time.time()
    if now - _S["beat"] >= 60:                       # proof of life
        _S["beat"] = now
        print("  [watcher] still waiting (%s). Screen shows: %s" % (_S["phase"][:40], [t[:30] for t in texts[:6]]))
    for pat in REFUSED:
        if pat in low:
            raise FlowProblem("refused", "YouTube Create refused this prompt/image", "softer_prompt")
    for pat in TRANSIENT:
        if pat in low:
            raise FlowProblem("error", "error shown on screen: " + pat, "retry")
    sig = hashlib.md5("|".join(sorted(set(t.lower() for t in texts))).encode()).hexdigest()
    changed = _S["sig"] is not None and sig != _S["sig"]
    _S["sig"] = sig
    if not (changed or now - _S["last_ai"] >= every):
        return
    _S["last_ai"] = now
    v = diagnose(d, texts)
    if not v or v.get("state") == "ok":
        return
    if v["state"] == "error":
        print("  [watcher] AI sees a problem:", v.get("reason"))
        raise FlowProblem("error", str(v.get("reason")), v.get("fix", "retry"))
    # blocked: clear it like a human, then keep waiting
    print("  [watcher] something is in the way:", v.get("reason"))
    if _S["assists"] >= MAX_GUARD_ASSISTS:
        raise FlowProblem("blocked", str(v.get("reason")), "retry")
    _S["assists"] += 1
    assist(d, "waiting: " + _S["phase"][:40], "the screen for '" + _S["phase"] + "' visible again with nothing blocking it",
           only_clear=True)
    _S["sig"] = None


def diagnose_failure(d, err):
    v = diagnose(d)
    if v and v.get("state") == "error":
        return FlowProblem("error", str(v.get("reason")), v.get("fix", "retry"))
    return FlowProblem("lost", "wrong screen: " + str(err)[:150], "retry")


# -------------------------------------------------------------------- prompt ----
def fix_prompt(p, script, old_prompt, problem, attempt):
    """Decide what to change before the next try."""
    print("  [watcher] problem: %s | reason: %s | plan: %s" % (problem.kind, problem.reason, problem.fix))
    blockers = ("login", "log in", "sign in", "password", "payment", "captcha", "verify", "account")
    if problem.fix == "give_up":
        if any(w in problem.reason.lower() for w in blockers):
            raise RuntimeError("Cannot continue: " + problem.reason)
        print("  [watcher] AI said give up, but it is not a login/payment problem -> trying again")
        problem.fix = "retry"
    if problem.fix == "retry" and attempt < 3:
        print("  [watcher] looks temporary -> same prompt again")
        return old_prompt
    print("  [watcher] rewriting the prompt to be safer for the video generator")
    st = (script or {}).get("style") or {}
    people = (script or {}).get("people", "none")
    overlay = (st.get("overlay") or "").strip()
    spoken = bool((script or {}).get("spoken_cta"))
    cta_hi = ((script or {}).get("cta_hi") or "").strip()
    has_voice = st.get("voice_check", "strict") != "none"
    drop_people = attempt >= 3          # first rewrites keep the video type chosen on the control page; later ones play it safe
    if people == "none" or drop_people:
        who = ("Show ONLY the product (toy/object) with simple motion and a plain background. "
               "Remove babies, children, faces and all people. ")
    elif people == "hands":
        who = "Keep it as it is: only an adult's hands (no face) handle the product. Remove babies, children and faces. "
    elif people == "adult_model":
        who = "Keep ONE adult Indian model using the product, but make it simple and modest. Remove babies, children and any famous-looking face. "
    else:
        who = "Keep one healthy adult pet playing with the product. Remove people, babies and children. "
    if st.get("adult_angle") and people in ("hands", "adult_model"):
        who += "Present it as a fun desk toy or stress-buster that grown-ups enjoy, never as a children's product. "
    if has_voice:
        voice = ("Keep the short spoken Hinglish line" + (" and the spoken call to action at the end (" + cta_hi + "). " if (spoken and cta_hi)
                 else ", and do NOT add a spoken call to action. "))
    else:
        voice = "Keep it with no voice, music or natural sound only. "
    text_rule = (("Keep this on-screen text instruction exactly: " + overlay + " ") if overlay else "No text on screen. ")
    ask = (
        "A video generator app refused or failed on this prompt. Rewrite it so it is accepted.\n"
        "Product: " + p["name"] + "\nFailure: " + problem.reason + "\n\nORIGINAL PROMPT:\n" + old_prompt[:1800] + "\n\n"
        "Rules for the rewrite: keep the same structure. " + voice + who + text_rule +
        "Remove brand names, medical or developmental claims (like 'helps kids learn/grow'), "
        "and anything about age safety. Keep it short and simple, under 880 characters in total.\n"
        "Return JSON: {\"prompt\": \"...\"}")

    def validate(t):
        try:
            n = len(json.loads(t).get("prompt", ""))
            return "prompt too short" if n <= 40 else ("prompt too long (%d characters, max 900)" % n if n > 900 else None)
        except Exception:
            return "not valid JSON"

    try:
        new = json.loads(llm.generate([ask], validate=validate))["prompt"]
        # make sure the choices from the control page survived the rewrite
        if overlay and "on-screen text" not in new.lower() and len(new) + len(overlay) + 1 <= 900:
            new = new.rstrip() + " " + overlay
        if spoken and has_voice and cta_hi and cta_hi not in new:
            add = " The voiceover must END with these exact words: " + cta_hi
            if len(new) + len(add) <= 900:
                new = new.rstrip() + add
        return new
    except Exception as e:
        print("  [watcher] could not rewrite the prompt:", str(e)[:100])
        return old_prompt
