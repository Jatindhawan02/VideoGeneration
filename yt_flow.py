"""YouTube Create: fixed steps, each VERIFIED from the UI. No AI used.
Standalone: python yt_flow.py "product name" "short details"
From code : yt_flow.run(d, prompt) -> path of the exported mp4 on the phone
"""
import os, re, sys, time
import xml.etree.ElementTree as ET
from appium.webdriver.common.appiumby import AppiumBy
import phone
from config import SHORT_PROMPT_STYLE

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")
YT_PKG = os.getenv("YTCREATE_PKG", "com.google.android.apps.youtube.producer")
B = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def nodes(d):
    out = []
    try:
        root = ET.fromstring(d.page_source)
    except Exception:
        return out
    for n in root.iter():
        m = B.match(n.attrib.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({"b": (x1, y1, x2, y2), "id": n.attrib.get("resource-id", ""),
                    "text": n.attrib.get("text", ""), "desc": n.attrib.get("content-desc", ""),
                    "cls": n.attrib.get("class", "")})
    return out


def find(d, text=None, desc=None, rid=None, cls=None, exact=False):
    def ok(want, have):
        if want is None:
            return True
        h = " ".join(have.split()).lower()
        w = " ".join(want.split()).lower()
        if exact:
            return h == w or h == w + " " + w
        return w in h
    return [n for n in nodes(d)
            if ok(text, n["text"]) and ok(desc, n["desc"]) and ok(rid, n["id"]) and (cls is None or n["cls"] == cls)]


def find_label(d, label, exact=True):
    return find(d, text=label, exact=exact) or find(d, desc=label, exact=exact)


def first_label(d, labels):
    for lb in labels:
        h = find_label(d, lb)
        if h:
            return lb, h[0]
    return None, None


def tap(d, n):
    x1, y1, x2, y2 = n["b"]
    d.execute_script("mobile: clickGesture", {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2})


import supervisor
import promptfit
_DRV = None
POPUP_NO = ["Not now", "No thanks", "Maybe later", "Later", "Don't allow", "Don\u2019t allow", "Deny", "Skip"]


def accept_consent(d):
    """YouTube Create shows an 'AI features / Privacy Notice' screen (Back / Continue) after Generate video.
    A normal user taps Continue. Only done when that notice text is really on screen."""
    try:
        low = " ".join((n["text"] or n["desc"]) for n in nodes(d)).lower().replace("\u2019", "'")
        if "ai features" in low and ("privacy notice" in low or "responsible for the content" in low):
            h = find_label(d, "Continue")
            if h:
                print("  AI privacy notice mila, 'Continue' dabaya")
                tap(d, h[0])
                time.sleep(3)
                return True
    except Exception:
        pass
    return False


ALLOW_LABELS = ["While using the app", "Allow all", "Allow"]


def accept_permission(d):
    """Android permission dialog (photos/camera/mic): the picker is empty if access is denied, so choose Allow."""
    try:
        low = " ".join((n["text"] or n["desc"]) for n in nodes(d)).lower().replace("\u2019", "'")
        if "don't allow" in low and "allow" in low:
            for lb in ALLOW_LABELS:
                h = find_label(d, lb)
                if h:
                    print("  permission dialog mila, '" + lb + "' dabaya")
                    tap(d, h[0])
                    time.sleep(2)
                    return True
    except Exception:
        pass
    return False


def dismiss_popups(d):
    try:
        if accept_consent(d):
            return True
        if accept_permission(d):
            return True
        for lb in POPUP_NO:
            h = find_label(d, lb)
            if h:
                print("  popup mila, '" + lb + "' dabaya")
                tap(d, h[0])
                time.sleep(2)
                return True
    except Exception:
        pass
    return False


def wait_for(fn, seconds=20, every=2):
    end = time.time() + seconds
    while time.time() < end:
        r = fn()
        if r:
            return r
        if _DRV is not None:
            dismiss_popups(_DRV)
            supervisor.guard(_DRV)
        time.sleep(every)
    return None


def seen(d):
    items = []
    for n in nodes(d):
        s = n["text"] or n["desc"]
        if s:
            items.append(s[:40])
    return items[:30]


def dump(d, name):
    os.makedirs(LOGS, exist_ok=True)
    try:
        with open(os.path.join(LOGS, "ytflow_" + name + ".xml"), "w", encoding="utf-8") as f:
            f.write(d.page_source)
        with open(os.path.join(LOGS, "ytflow_" + name + ".png"), "wb") as f:
            f.write(phone.screenshot(d))
        print("  saved logs\\ytflow_" + name + ".xml and .png")
    except Exception as e:
        print("  dump failed:", str(e)[:80])


def _run_step_once(d, name, action, verify, retries, settle):
    for i in range(1, retries + 1):
        try:
            action()
        except Exception as e:
            print("  (" + name + " try " + str(i) + ": action error: " + str(e)[:100] + ")")
        time.sleep(settle)
        dismiss_popups(d)
        got = None
        try:
            got = verify()
        except Exception as e:
            print("  verify error:", str(e)[:100])
        if got:
            print("[OK] " + name + " (verified: " + str(got)[:80] + ")")
            return got
        if i == 1 and name not in _DUMPED:
            _DUMPED.add(name)
            print("  (first try did not verify: saving what the screen shows)")
            dump(d, name.replace(" ", "_") + "_try1")
    return None


AI_ROUNDS = int(os.getenv("AI_ROUNDS", "3"))
_DUMPED = set()
_CTX = {}


def run_step(d, name, action, verify, expected, retries=3, settle=2):
    """Fixed steps first (fast, free). If the screen is not as expected, the AI clears the way like a human,
    then the fixed steps run again. Only after AI_ROUNDS failed rounds does the step give up."""
    supervisor.set_phase("step: " + name + " (expecting: " + expected + ")")
    for rnd in range(AI_ROUNDS + 1):
        got = _run_step_once(d, name, action, verify, retries, settle)
        if got:
            return got
        if rnd == AI_ROUNDS:
            break
        print("[..] " + name + ": screen is not as expected -> AI helps (round %d/%d)" % (rnd + 1, AI_ROUNDS))
        supervisor.assist(d, name, expected, prompt=_CTX.get("prompt"), max_steps=6, only_clear=True)
    print("[FAIL] " + name + ": expected -> " + expected)
    print("       saw on screen ->", seen(d))
    dump(d, name.replace(" ", "_"))
    raise RuntimeError(name + " failed: expected " + expected)


def find_plus(d):
    """The + button that adds the reference picture: labelled 'Add media' or 'Upload up to 3 ingredients'."""
    return find(d, desc="Add media", exact=True) or find_label(d, "ingredients", exact=False)


def list_mp4():
    out = phone.adb("shell", "find /sdcard/Movies /sdcard/DCIM /sdcard/Download /sdcard/Pictures -name '*.mp4' 2>/dev/null")
    return set(l.strip() for l in out.splitlines() if l.strip().startswith("/"))


def field(d):
    f = find(d, cls="android.widget.EditText")
    return f[0] if f else None


def wait_stable(path, tries=15):
    """Wait until the exported file stops growing (export really finished)."""
    last = None
    for _ in range(tries):
        try:
            size = (phone.adb("shell", "stat -c %s '" + path + "'") or "").strip()
        except Exception:
            size = ""
        if size and size == last and size.isdigit() and int(size) > 0:
            return True
        last = size
        time.sleep(2)
    return False


def run(d, prompt, n_images=3):
    """n_images = how many reference pictures were pushed to the phone (the newest ones are picked)."""
    global _DRV
    _DRV = d
    LIMIT = int(os.getenv("YT_PROMPT_MAX", "880"))        # the app refuses more than 900 characters
    if len(prompt) > LIMIT:
        short = promptfit.fit(prompt, LIMIT)
        print("  prompt was %d characters, shortened to %d (the app allows 900; voice and call to action kept)" % (len(prompt), len(short)))
        prompt = short
    _CTX["prompt"] = prompt
    _DUMPED.clear()
    supervisor.reset()
    supervisor.set_phase("starting YouTube Create and opening Generate video")
    # 0 always start from a clean app. A run (or a retry) can leave the "video exported" screen open,
    #   and then there is no 'Generate video' button to tap.
    phone.adb("shell", "am force-stop " + YT_PKG)
    time.sleep(2)
    # 1 open app, tap "Generate video"
    phone.open_app(d, YT_PKG)
    wait_for(lambda: find_label(d, "Generate video", exact=True), 25)

    def tap_gen():
        h = find_label(d, "Generate video")
        if h:
            tap(d, h[0])
    run_step(d, "open generate video", tap_gen,
             lambda: "text box visible" if field(d) else None,
             "a prompt text box (EditText)", settle=4)

    # 2 type the prompt (verified: text is really inside the box)
    def type_prompt():
        f = field(d)
        if not f:
            return
        tap(d, f)
        time.sleep(1)
        try:
            d.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText").clear()     # never type on top of old text
        except Exception:
            pass
        try:
            d.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText").send_keys(prompt)
        except Exception:
            d.execute_script("mobile: type", {"text": prompt})

    def prompt_ok():
        f = field(d)
        # the START and the END must both be in the box (the end holds the call to action; a cut-off text would lose it)
        ok = f and prompt[:25].lower() in f["text"].lower() and prompt[-20:].lower() in f["text"].lower()
        return "prompt is in the box" if ok else None
    run_step(d, "type prompt", type_prompt, prompt_ok, "the prompt text inside the text box")

    # 3 open the media picker with + / Add media
    def open_picker():
        h = find_plus(d)
        if h:
            tap(d, h[0])
    run_step(d, "open media picker", open_picker,
             lambda: "thumbnails visible" if find(d, rid="mediaThumbnail") else None,
             "image thumbnails after tapping Add media", settle=4)

    # 4 select the newest image ONCE (never tap an already selected one)
    def pick_image():
        if find(d, desc="selected"):
            return
        t = find(d, rid="mediaThumbnail-0")
        if t:
            tap(d, t[0])
    run_step(d, "select image", pick_image,
             lambda: "image shows selected" if find(d, desc="selected") else None,
             "an element whose description says 'selected'")
    # optional: add up to 2 more product photos (never fails the run)
    try:
        for idx in range(1, max(1, min(3, n_images))):      # only the pictures we pushed, never old gallery photos
            before = len(find(d, desc="selected"))
            t = [n for n in find(d, rid="mediaThumbnail-%d" % idx) if n["id"].endswith("mediaThumbnail-%d" % idx)]
            if not t:
                break
            if "selected" in t[0]["desc"].lower():
                continue
            tap(d, t[0])
            time.sleep(2)
            after = len(find(d, desc="selected"))
            print("  extra photo %d: selected count %d -> %d" % (idx, before, after))
            if after <= before:
                t0 = [n for n in find(d, rid="mediaThumbnail-0") if n["id"].endswith("mediaThumbnail-0")]
                if t0 and "selected" not in t0[0]["desc"].lower():
                    tap(d, t0[0])
                    time.sleep(2)
                break
    except Exception as e:
        print("  (extra photos skipped:", str(e)[:80], ")")

    # 5 confirm the image (button name unknown, so try common names, exact match only)
    def confirm_image():
        lb, h = first_label(d, ["Add", "Done", "Next", "Insert", "Select", "Confirm", "Use"])
        if h:
            print("  tapping:", lb)
            tap(d, h)
    run_step(d, "confirm image", confirm_image,
             lambda: "picker closed, prompt box visible" if (not find(d, rid="mediaThumbnail") and field(d)) else None,
             "media picker closed and the prompt box visible again", settle=3)

    # 6 start generation
    def start_gen():
        lb, h = first_label(d, ["Generate", "Create video", "Create"])
        if h:
            print("  tapping:", lb)
            tap(d, h)
    run_step(d, "start generation", start_gen,
             lambda: "generation started" if (find(d, text="generating") or find(d, text="creating")
                                              or find_label(d, "Use video") or not field(d)) else None,
             "the prompt screen to change into a progress screen", settle=4)

    # 7 wait until "Use video" exists (up to 12 min)
    print("  waiting for the video (up to 12 minutes)...")
    supervisor.set_phase("waiting for the AI to generate the video (a spinner/progress is normal; a Use video button appears when ready)")
    ready = wait_for(lambda: find_label(d, "Use video"), seconds=720, every=10)
    if not ready:
        print("[FAIL] wait for video: no 'Use video' button appeared. Saw:", seen(d))
        dump(d, "wait_video")
        raise supervisor.FlowProblem("timeout", "no 'Use video' button after 12 minutes", "retry")
    print("[OK] video ready (verified: 'Use video' button is on screen)")

    # 8 Use video -> Export -> new mp4 must appear on the phone
    supervisor.set_phase("opening the editor and exporting the video to the phone (Use video, Export, Save to device)")
    before = list_mp4()

    def use_video():
        h = find_label(d, "Use video")
        if h:
            tap(d, h[0])
    run_step(d, "tap use video", use_video,
             lambda: "export button visible" if find_label(d, "Export") else None,
             "an 'Export' button after Use video", settle=4)

    def export():
        h = find_label(d, "Export")
        if h:
            tap(d, h[0])
    export()
    wait_for(lambda: find_label(d, "Save to device") or find_label(d, "Upload to YouTube Shorts"), 15, 2)
    if not find_label(d, "Save to device") and (find_label(d, "Upload to YouTube Shorts")
                                                or find(d, text="only be uploaded") or find(d, desc="only be uploaded")):
        print("[FAIL] save to device: YouTube is offering ONLY 'Upload to YouTube Shorts' (no Save to device).")
        dump(d, "no_save_option")
        raise supervisor.FlowProblem("export_blocked", "this account cannot export: generated videos can only be uploaded to YouTube", "give_up")

    def save_dev():
        h = find_label(d, "Save to device")
        if h:
            tap(d, h[0])
    run_step(d, "save to device", save_dev,
             lambda: "sheet closed or saving started" if not find_label(d, "Save to device") else None,
             "the 'Save to device' button to be tapped", retries=2, settle=3)
    print("  exporting, waiting for a new mp4 on the phone (up to 5 minutes)...")
    new = wait_for(lambda: sorted(list_mp4() - before), seconds=300, every=8)
    if not new:
        print("[FAIL] export: no new mp4 appeared. Saw:", seen(d))
        dump(d, "export")
        raise RuntimeError("export did not create a new mp4")
    path = sorted(new)[-1]                      # newest file (names contain the date and time)
    wait_stable(path)
    print("[OK] export (verified: new file on phone: " + path + ")")
    phone.adb("shell", "am force-stop " + YT_PKG)   # leave the app closed, so the next run starts clean
    return path


def main():
    if len(sys.argv) < 2:
        print('Use: python yt_flow.py "product name" "short details"')
        return
    prompt = (SHORT_PROMPT_STYLE + " Product: " + sys.argv[1] + ". Details: " + (sys.argv[2] if len(sys.argv) > 2 else "")).strip()
    d = phone.connect()
    try:
        path = run(d, prompt)
        print("\nDONE. Video on phone:", path)
    except (Exception, supervisor.FlowProblem) as e:
        print("\nSTOPPED:", e)
    finally:
        try:
            d.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
