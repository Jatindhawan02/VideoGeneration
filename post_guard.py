"""post_guard.py - posts the Instagram Reel, but CHECKS the phone screen at every stage and never taps Share
unless everything is right. Mistakes are written to memory/mistakes.json and shown to the AI on every later run.

Stages (each one is verified before the next starts):
  1. open the new-Reel gallery picker          -> check: it is the REEL creator and the gallery is visible
  2. pick the newest VIDEO (not a photo)       -> the CODE knows it is a video: only a thumbnail with a duration (0:10) is tapped.
                                                  The editor screens after it show NO duration, so they are never used to tell photo from video.
  3. Next ... Next, type the caption           -> check: final screen, caption text is in the box, it is a video
  4. look at your notes one last time, then Share (tapped by code, never by the AI)
  5. check that the Reel is really uploading

If a check fails the bot backs out, writes the mistake down, and tries again from the start (max 3 rounds).
If it still fails it does NOT share. After Share was tapped it never retries (that would post twice).

Used from main_link.py:   ok, why = post_guard.post_reel(d, cap, video_name)
"""
import json, os, re, time
from google.genai import types
import agent, llm, phone

IG_PKG = "com.instagram.android"
DURATION = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)")        # 0:08, 1:02 ... the badge on video thumbnails
MAX_ROUNDS = 3
SCOPE = "ig"                                                 # mistakes with this scope are shown to every "ig_*" task

# What the code already knows about the chosen file. A duration like 0:10 exists ONLY on the gallery thumbnail: after the tap, the
# editor / caption screens show no duration at all. So "video or photo" is decided ONCE, in the gallery, by code, and never again by looking.
STATE = {"video_tapped": False, "how": ""}
VIDEO_NOTE = ("\nIMPORTANT: a duration like 0:10 is shown ONLY on gallery thumbnails. After a video is selected, the editor, preview and "
              "caption screens normally show NO duration. A missing duration on this screen does NOT mean the item is a photo: never judge "
              "photo or video from it.")


# ------------------------------------------------------------------ AI eyes ----
def look(d, questions, extra="", width=720):
    """Ask the AI yes/no questions about the CURRENT screen. questions = {key: question}.
    Returns {key: bool, ..., "seen": str} or None when the AI could not answer (then nothing is trusted)."""
    keys = list(questions)
    prompt = ("This is a screenshot of the Instagram app on an Android phone.\n"
              "Answer every question with true or false. Look carefully, do not guess: if you cannot see it, answer false.\n"
              + "\n".join("- %s: %s" % (k, questions[k]) for k in keys) + "\n" + extra +
              "\nReturn JSON: {" + ", ".join('"%s": true/false' % k for k in keys) + ', "seen": "one short sentence: what the screen shows"}')

    def validate(text):
        try:
            data = json.loads(text)
        except Exception:
            return "not valid JSON"
        for k in keys:
            if not isinstance(data.get(k), bool):
                return "'%s' must be true or false" % k
        return None

    for _ in range(3):
        try:
            png = phone.screenshot(d)
            part = types.Part.from_bytes(data=agent.shrink(png, width), mime_type="image/jpeg")
            return json.loads(llm.generate([part, prompt], validate=validate))
        except Exception as e:
            print("  [guard] the check could not run (%s) -> trying again in 10 s" % str(e)[:90])
            time.sleep(10)
    return None


def _remember(what, rule):
    print("  [guard] MISTAKE: " + what)
    agent.record_mistake(SCOPE, what, rule)


# ----------------------------------------------------------------- free eyes ----
def video_badges(nodes, W, H):
    """Pure helper: elements of the gallery GRID (lower part of the screen) that show a duration like 0:08 = video thumbnails.
    Sorted top-left first = the newest video (Recents lists the newest first)."""
    out = []
    for n in nodes:
        txt = (n.get("text") or "") + " " + (n.get("desc") or "")
        x1, y1, x2, y2 = n["b"]
        if DURATION.search(txt) and y1 > 0.08 * H and y2 < 0.97 * H:
            out.append(n)
    out.sort(key=lambda n: (n["b"][1] // max(1, int(0.04 * H)), n["b"][0]))     # rows first, then left to right
    return out


def exact_button(nodes, label, W, H):
    """Pure helper: the element whose text or description is EXACTLY `label` (so 'Share' is not 'Share to Facebook').
    If several, the lowest one on the screen. Returns (cx, cy) pixels or None."""
    label = label.strip().lower()
    hits = [n for n in nodes if (n.get("text") or "").strip().lower() == label or (n.get("desc") or "").strip().lower() == label]
    if not hits:
        return None
    n = max(hits, key=lambda n: n["b"][3])
    return (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2


def tile_for(nodes, badge, W, H):
    """Pure helper: the whole thumbnail (tile) that contains a duration badge, so we tap the tile centre, not the tiny badge."""
    bx, by = (badge["b"][0] + badge["b"][2]) / 2, (badge["b"][1] + badge["b"][3]) / 2
    best = None
    for n in nodes:
        x1, y1, x2, y2 = n["b"]
        w, h = x2 - x1, y2 - y1
        if x1 <= bx <= x2 and y1 <= by <= y2 and 0.15 * W <= w <= 0.6 * W and w * h < 0.3 * W * H:
            if best is None or w * h < (best["b"][2] - best["b"][0]) * (best["b"][3] - best["b"][1]):
                best = n
    return best or badge


def grid_tiles(nodes, W, H):
    """Pure helper: the thumbnails of the gallery grid, found by size (the most common tile size on the screen).
    Sorted row by row, left to right = newest first. Works even when the tree has no duration text."""
    cand = []
    for n in nodes:
        x1, y1, x2, y2 = n["b"]
        w, h = x2 - x1, y2 - y1
        if y1 > 0.08 * H and y2 < 0.97 * H and 0.15 * W <= w <= 0.55 * W and 0.06 * H <= h <= 0.45 * H:
            cand.append(n)
    if not cand:
        return []
    groups = {}
    for n in cand:
        key = (round((n["b"][2] - n["b"][0]) / 12), round((n["b"][3] - n["b"][1]) / 12))
        groups.setdefault(key, []).append(n)
    best = max(groups.values(), key=len)
    if len(best) < 4:
        return []
    seen, out = set(), []
    for n in sorted(best, key=lambda n: (n["b"][1], n["b"][0])):
        if n["b"] not in seen:
            seen.add(n["b"])
            out.append(n)
    return out


def _mark_tiles(png, tiles, W):
    """Draw a big number in the TOP-LEFT corner of every tile (the duration badge is bottom-right, so it stays visible)."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(io.BytesIO(png)).convert("RGB")
    k = im.width / float(W)
    dr = ImageDraw.Draw(im)
    size = max(28, int(im.width * 0.055))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        try:
            font = ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
    for i, n in enumerate(tiles):
        x1, y1 = n["b"][0] * k, n["b"][1] * k
        dr.rectangle([x1 + 4, y1 + 4, x1 + size * 1.5, y1 + size * 1.3], fill=(220, 0, 0))
        dr.text((x1 + 10, y1 + 6), str(i + 1), fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def ai_video_tile(d, tiles, W):
    """AI looks at the numbered tiles and says WHICH NUMBERS show a video duration badge. The AI never gives coordinates:
    the code taps the tile. Returns the index (0-based) of the first = newest video tile, or None."""
    tiles = tiles[:12]
    for attempt in range(3):
        try:
            png = phone.screenshot(d)
            marked = _mark_tiles(png, tiles, W)
            prompt = ("This is the gallery picker of the Instagram app (Android). Every thumbnail has a RED NUMBER in its top-left corner.\n"
                      "A VIDEO thumbnail shows a small duration label such as 0:08 or 0:15 in its BOTTOM corner (often bottom-right, or with a play icon). "
                      "A PHOTO thumbnail has NO duration label at all. Screenshots of an app/product page are PHOTOS.\n"
                      "List the numbers of ALL thumbnails that show a duration label. Do not guess: if you cannot see a duration on a tile, do not list it.\n"
                      'Return JSON: {"video_tiles": [numbers], "seen": "one short sentence"}')

            def validate(text):
                try:
                    data = json.loads(text)
                except Exception:
                    return "not valid JSON"
                v = data.get("video_tiles")
                if not isinstance(v, list) or not all(isinstance(x, int) and 1 <= x <= len(tiles) for x in v):
                    return "video_tiles must be a list of tile numbers between 1 and %d" % len(tiles)
                return None

            part = types.Part.from_bytes(data=agent.shrink(marked, 1000), mime_type="image/jpeg")
            data = json.loads(llm.generate([part, prompt], validate=validate))
            print("  [guard] AI sees video duration on tiles %s (%s)" % (data["video_tiles"], str(data.get("seen"))[:80]))
            return (min(data["video_tiles"]) - 1) if data["video_tiles"] else None
        except Exception as e:
            print("  [guard] tile check could not run (%s) -> again in 8 s" % str(e)[:90])
            time.sleep(8)
    return None


def _ascii_snippet(cap, n=25):
    """The first letters of the caption AS THEY WERE REALLY TYPED: the typer drops emojis, non-ASCII letters, % and backslash,
    so the check must not look for them (an emoji at the start of the caption made the check fail before)."""
    t = cap.encode("ascii", "ignore").decode().replace("%", "").replace("\\", "")
    return re.sub(r"\s+", " ", t).strip()[:n]


PLACEHOLDER = ("write a caption", "add a caption")


def caption_state(nodes, snippet):
    """Pure helper, free: True = the typed caption text is in the screen tree, False = the grey placeholder is still showing (box empty),
    None = the tree cannot tell (then the AI decides)."""
    probe = agent._norm(snippet)[:12]
    placeholder = False
    for n in nodes:
        label = agent._norm((n.get("text") or "") + " " + (n.get("desc") or ""))
        if probe and probe in label:
            return True
        if any(p in label for p in PLACEHOLDER):
            placeholder = True
    return False if placeholder else None


def _tap_px(d, cx, cy, W, H):
    phone.tap_norm(d, int(round(cx * 1000 / W)), int(round(cy * 1000 / H)))
    time.sleep(2)


# -------------------------------------------------------------------- stages ----
def _restart_instagram(d):
    try:
        phone.adb("shell", "am", "force-stop", IG_PKG)
        time.sleep(2)
    except Exception:
        pass
    phone.open_app(d, IG_PKG)
    time.sleep(5)


def _open_picker(d):
    ok, why = agent.run_task(
        d,
        "In Instagram, open the screen for creating a NEW REEL: tap the + (create) icon. If tabs POST / STORY / REEL are shown, "
        "make sure REEL is selected. Done when the gallery picker of a new Reel is showing (a grid of recent photos and videos). "
        "Do NOT choose any file yet and never tap Share.",
        max_steps=15, key="ig_open")
    if not ok:
        agent.forget_flow("ig_open")
        return False, "could not open the Reel creator: " + str(why)[:120]
    r = look(d, {"creating_reel": "Is this the screen for creating a NEW REEL (REEL tab selected or a title like 'New reel'), not a normal post, story or the home feed?",
                 "gallery_visible": "Is a gallery grid of recent photos/videos visible?"})
    if r is None:
        return False, "could not verify the Reel creator screen (AI unavailable)"
    if not (r["creating_reel"] and r["gallery_visible"]):
        agent.forget_flow("ig_open")
        _remember("Opened the wrong creator screen (%s)." % r.get("seen", "?"),
                  "After tapping +, select the REEL tab and wait for the gallery grid before choosing a file.")
        return False, "wrong screen: " + str(r.get("seen"))
    return True, "reel picker open"


def _pick_video(d):
    STATE.update(video_tapped=False, how="")
    W, H = agent._screen(d)
    nodes = []
    for _ in range(4):                       # wait until the grid is really loaded
        nodes = agent._ui_nodes(d)
        if video_badges(nodes, W, H) or grid_tiles(nodes, W, H):
            break
        time.sleep(2)
    target, how = None, ""

    # 1) free: a duration text in the screen tree
    cands = video_badges(nodes, W, H)
    if cands:
        target = tile_for(nodes, cands[0], W, H)
        how = "duration badge '%s' in the screen tree" % ((cands[0].get("text") or cands[0].get("desc") or "")[:30]).strip()

    # 2) the tree shows no duration: the AI looks at the NUMBERED tiles and says which ones have a duration; the code taps the tile
    if target is None:
        tiles = grid_tiles(nodes, W, H)
        print("  [guard] no duration in the screen tree; %d gallery tiles found -> AI checks the numbered tiles" % len(tiles))
        if tiles:
            idx = ai_video_tile(d, tiles, W)
            if idx is not None:
                target, how = tiles[idx], "AI saw a duration on numbered tile %d" % (idx + 1)
            else:
                return False, "no thumbnail with a duration among the first %d tiles (is the video in the gallery? not scanned yet?)" % min(12, len(tiles))

    if target is not None:
        cx, cy = (target["b"][0] + target["b"][2]) / 2, (target["b"][1] + target["b"][3]) / 2
        print("  [guard] newest video: %s -> tapping the tile at (%d, %d)" % (how, cx, cy))
        _tap_px(d, cx, cy, W, H)
        STATE.update(video_tapped=True, how=how)
    else:
        # 3) last resort: tiles not detectable at all
        print("  [guard] tiles not detectable -> the AI picks, with a strict rule")
        ok, why = agent.run_task(
            d,
            "In the gallery grid tap the NEWEST VIDEO: a thumbnail that shows a duration such as 0:08 in its corner. "
            "NEVER tap a thumbnail without a duration (that is a photo). Done when that video is selected. Never tap Share.",
            max_steps=8, key=None)
        if not ok:
            return False, "could not pick a video: " + str(why)[:120]
        STATE.update(video_tapped=True, how="the AI tapped a thumbnail with a duration")
    time.sleep(2)
    # Only check that we are still in the Reel flow. Photo or video is NOT asked here: the duration is gone after the tap.
    r = look(d, {"in_reel_flow": "Is this the new-Reel flow: the gallery picker with an item selected, or the Reel editor / preview / next screen "
                                 "(NOT the home feed, the camera, a Story or Post creator, a permission popup or an error)?"}, extra=VIDEO_NOTE)
    if r is None:
        return False, "could not verify the screen after picking the video (AI unavailable)"
    if not r["in_reel_flow"]:
        _remember("After picking the video the screen was not the Reel flow (%s)." % r.get("seen", "?"),
                  "After tapping the video thumbnail the Reel editor opens and shows NO duration: that is normal. Only go back if the screen is not the Reel flow at all.")
        return False, "left the Reel flow after picking the video: " + str(r.get("seen"))
    return True, "video selected (" + STATE["how"] + ")"


def _final_checks(d, cap, typed=False):
    """Free screen-tree check first (reliable), AI only for what the tree cannot tell."""
    snippet = _ascii_snippet(cap)
    W, H = agent._screen(d)
    nodes = agent._ui_nodes(d)
    tree_final = _caption_box(nodes, W, H) is not None and composer_share(nodes, W, H) is not None
    tree_cap = caption_state(nodes, snippet)
    known = STATE.get("video_tapped")
    if tree_final and known and tree_cap is not None:
        return {"final_screen": True, "is_video": True, "caption_present": tree_cap, "seen": "checked from the screen tree (no AI)"}

    cap_q = ("Does the caption box contain real typed text, and NOT just the grey placeholder 'Write a caption and add hashtags...'? "
             "The words may be cut off with '...' or be long: any real text that is not the placeholder means true. "
             "(The caption starts with: \"%s\")" % snippet)
    qs = {"final_screen": "Is this the FINAL screen before sharing a Reel: a caption box and a 'Share' button are visible?",
          "caption_present": cap_q}
    if not known:       # only when the code could not confirm the video in the gallery
        qs["is_video"] = "Is the thing being posted a video / Reel and not a single still photo? (the duration is not shown on this screen)"
    r = look(d, qs, extra=VIDEO_NOTE)
    if r is None:
        return None
    if known:
        r["is_video"] = True            # decided in the gallery by the duration badge, not by this screenshot
    if tree_final:
        r["final_screen"] = True
    if tree_cap is not None:
        r["caption_present"] = tree_cap
    elif not r["caption_present"] and typed:
        print("  [guard] AI says the caption is missing but it was just typed -> second look at a higher resolution")
        time.sleep(1.5)
        r2 = look(d, {"caption_present": cap_q}, extra=VIDEO_NOTE, width=1080)
        if r2 and r2["caption_present"]:
            r["caption_present"] = True
    return r


def composer_share(nodes, W, H):
    """The big 'Share' button of the Reel composer: exact label AND wide AND in the bottom part of the screen.
    The small paper-plane icon on a playing reel (description 'Share') is neither, so it can never match."""
    hits = []
    for n in nodes:
        lab = (n.get("text") or "").strip().lower(), (n.get("desc") or "").strip().lower()
        if "share" in lab:
            x1, y1, x2, y2 = n["b"]
            if (x2 - x1) >= 0.25 * W and y1 >= 0.70 * H:
                hits.append(n)
    if not hits:
        return None
    n = max(hits, key=lambda n: n["b"][3])
    return (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2


def _caption_box(nodes, W, H):
    """Pure helper: the caption box of the final Reel screen, found by its text / id (never by an AI guess of coordinates)."""
    best, best_score = None, 0
    for n in nodes:
        label = ((n.get("text") or "") + " " + (n.get("desc") or "")).lower()
        rid = (n.get("id") or "").lower()
        x1, y1, x2, y2 = n["b"]
        if (x2 - x1) < 0.3 * W or (y2 - y1) > 0.6 * H:
            continue
        score = 0
        if "write a caption" in label or "add a caption" in label:
            score = 3
        elif "caption" in rid:
            score = 2
        elif "caption" in label and "captions" not in label:
            score = 1
        if score > best_score:
            best, best_score = n, score
    return best


def _composer_visible(d):
    """Free check (screen tree): are the caption box AND the Share button on the screen = still on the final Reel screen?"""
    try:
        W, H = agent._screen(d)
        nodes = agent._ui_nodes(d)
        return _caption_box(nodes, W, H) is not None and composer_share(nodes, W, H) is not None
    except Exception:
        return False


def _keyboard_open(d):
    """Is the on-screen keyboard REALLY open? Appium sometimes says False while it is open and dumpsys sometimes says True
    after it closed, so a screenshot decides: the AI only has to see rows of letter keys."""
    try:
        if d.is_keyboard_shown():
            return True
    except Exception:
        pass
    r = look(d, {"keyboard_open": "Is the on-screen keyboard (rows of letter keys, usually in the lower part of the screen) visible?"}, width=540)
    if r is not None:
        return bool(r["keyboard_open"])
    return agent._keyboard_shown(d)         # AI not available: fall back to the old signals


def _close_keyboard(d):
    """Close the keyboard and make SURE it is closed. Returns True when no keyboard is open any more.
    1) hide_keyboard  2) if the screenshot still shows it, ONE Back press (safe: Back only closes the keyboard while it is visible).
    Back is never pressed when no keyboard is visible."""
    for attempt in (1, 2):
        if not _keyboard_open(d):
            if attempt > 1:
                print("  [guard] keyboard closed")
            return True
        print("  [guard] keyboard is open -> closing it (try %d)" % attempt)
        # hide_keyboard is unsupported on this Appium setup (Unknown mobile command), so go straight to Back.
        phone.adb("shell", "input keyevent 4")          # the keyboard was just seen on a screenshot, so Back closes only the keyboard
        time.sleep(1.5)
    return not _keyboard_open(d)


def _type_caption(d, cap):
    """On the final screen: tap the caption box found in the screen tree, wait for the keyboard, type the caption, close the keyboard."""
    agent.reset_ime()
    W, H = agent._screen(d)
    opened = False
    for attempt in range(1, 4):
        box = _caption_box(agent._ui_nodes(d), W, H)
        if box:
            x1, y1, x2, y2 = box["b"]
            cx, cy = x1 + min((x2 - x1) * 0.3, 300), (y1 + y2) / 2
            print("  [guard] caption box found in the screen tree at (%d, %d) -> tapping it (try %d)" % (cx, cy, attempt))
            _tap_px(d, cx, cy, W, H)
        else:
            print("  [guard] caption box not found in the screen tree -> asking the AI to tap it (try %d)" % attempt)
            agent.run_task(d, "On the final Reel screen tap the caption box (it shows 'Write a caption and add hashtags...') ONCE so the keyboard opens. "
                              "Do NOT type anything and never tap Share.", max_steps=5, key=None)
        time.sleep(1.5)
        if agent._keyboard_shown(d):
            opened = True
            break
    if not opened:
        print("  [guard] the keyboard was not detected as open -> typing anyway, the result is checked after")
    res = agent._type_text(d, cap)
    if res is False:
        return False, "the caption text did not appear in the caption box"
    if not _close_keyboard(d):           # the keyboard hides the Share button
        return False, "the keyboard could not be closed after typing the caption"
    return True, "caption typed"


def _caption_screen(d, cap):
    ok, why = agent.run_task(
        d,
        "A VIDEO is selected for a new Reel (already confirmed). Editing screens do NOT show its duration: that is normal, so NEVER go back, "
        "never pick another file and never restart because no duration is visible. "
        "Tap Next (or OK / Continue / Skip) on every editing, music, effects or sticker screen and add "
        "nothing, until the FINAL screen with a caption box and a Share button appears. "
        "Do NOT tap the caption box and do NOT type anything: the program does that. Do NOT tap Share. "
        "Done when the FINAL screen is visible (a caption box with grey placeholder text, and a Share button).",
        max_steps=25, key=None)
    if not ok:
        return False, "could not reach the caption screen: " + str(why)[:120]
    r = _final_checks(d, cap)
    if r is None:
        return False, "could not verify the final screen (AI unavailable)"
    if r["final_screen"] and r["is_video"] and not r["caption_present"]:
        print("  [guard] final screen reached -> the program types the caption now")
        okc, whyc = _type_caption(d, cap)
        if not okc:
            _remember("The caption could not be typed (%s)." % whyc,
                      "On the final screen tap the caption box and type the caption BEFORE Share. Check that the text is in the box.")
            return False, whyc
        r = _final_checks(d, cap, typed=True)
        if r is None:
            return False, "could not verify the final screen (AI unavailable)"
    if not r["final_screen"]:
        _remember("Did not end on the final Share screen (%s)." % r.get("seen", "?"), "Keep tapping Next until the caption box and the Share button are visible.")
        return False, "not on the final screen: " + str(r.get("seen"))
    if not r["is_video"]:
        _remember("The post on the final screen was a photo, not a video.", "Never continue with a photo: go back and pick the thumbnail with a duration.")
        return False, "the post is a photo, not a video"
    if not r["caption_present"]:
        _remember("The caption is still missing on the final screen.", "Type the caption and confirm it is visible in the box before Share.")
        return False, "the caption is not in the box"
    return True, "final screen ok"


def _handle_notes(d):
    """Your notes count as a HOLD: nothing is shared while there is a note the bot has not acted on."""
    for _ in range(3):
        note = agent._step_note()
        if not note:
            return
        print("  [guard] you sent a note, handling it before Share: " + note)
        agent.run_task(d, "The user says: " + note + ". Fix exactly this on the current screen. Do NOT tap Share.", max_steps=10, key=None)
        time.sleep(1)


def _ai_find_share(d):
    """Last resort when the tree has no 'Share' button: the AI points at it. Only accepted in the lower part of the screen."""
    def validate(text):
        try:
            data = json.loads(text)
        except Exception:
            return "not valid JSON"
        if not isinstance(data.get("found"), bool):
            return "'found' must be true or false"
        if data["found"]:
            try:
                if not (0 <= float(data["x"]) <= 1000 and 0 <= float(data["y"]) <= 1000):
                    return "x/y must be between 0 and 1000"
            except Exception:
                return "x/y must be numbers"
        return None
    for _ in range(2):
        try:
            png = phone.screenshot(d)
            part = types.Part.from_bytes(data=agent.shrink(png, 900), mime_type="image/jpeg")
            data = json.loads(llm.generate([part, "Screenshot of the final screen of the Instagram Reel creator. Find the blue 'Share' button "
                                            "(NOT 'Share to Facebook', not a share icon). Coordinates are normalized 0-1000, top-left origin, centre of the button. "
                                            'Return JSON: {"found": true/false, "x": 0-1000, "y": 0-1000}'], validate=validate))
            if data["found"] and float(data["y"]) >= 650:
                return int(round(float(data["x"]))), int(round(float(data["y"])))
            return None
        except Exception as e:
            print("  [guard] Share finder could not run (%s)" % str(e)[:90])
            time.sleep(5)
    return None


_DONE_WORDS = re.compile(r"sharing to reels|sharing\b|your reel (is|was|has been)|reel (was )?shared|shared to|uploading|posting to|finishing up", re.I)
_BAD_WORDS = re.compile(r"couldn.t share|could not share|try again|something went wrong|discard|failed", re.I)


def _tap_share(d, strict=False):
    """Tap the Share button: screen tree first, then text, then the AI points at it. Returns True when a tap was made."""
    W, H = agent._screen(d)
    hit = composer_share(agent._ui_nodes(d), W, H)
    if hit:
        print("  [guard] tapping Share (found in the screen tree) at (%d, %d)" % (hit[0], hit[1]))
        _tap_px(d, hit[0], hit[1], W, H)
        return True
    if strict:
        return False                                # retry: only the real composer button, never a text/AI guess
    if agent.tap_text(d, ["Share"], timeout=6, avoid_bottom=False):
        print("  [guard] Share tapped by text")
        return True
    pt = _ai_find_share(d)
    if pt:
        print("  [guard] Share not in the tree -> the AI pointed at it: %s -> tapping" % (pt,))
        phone.tap_norm(d, pt[0], pt[1])
        return True
    return False


def _share_state(d):
    """Free (screen tree): started = upload text or the final screen is gone, error = an error/discard text,
    still = the final screen is still there."""
    try:
        nodes = agent._ui_nodes(d)
        W, H = agent._screen(d)
    except Exception:
        return "unknown", ""
    blob = " | ".join(((n.get("text") or "") + " " + (n.get("desc") or "")).strip() for n in nodes)
    m = _BAD_WORDS.search(blob)
    if m:
        return "error", m.group(0)
    if _caption_box(nodes, W, H) is not None and composer_share(nodes, W, H) is not None:
        return "still", ""                  # the composer is still there: not shared yet, whatever other text says
    m = _DONE_WORDS.search(blob)
    if m:
        return "started", m.group(0)
    if not nodes:
        return "unknown", ""                # empty tree (screen changing): do not guess
    return "started", "the final screen is gone, Instagram moved on"


def _share(d, cap):
    _handle_notes(d)
    if not _close_keyboard(d):                      # a Share tap while the keyboard is open lands on the keys / does nothing
        return False, "NOT shared: the keyboard could not be closed"
    r = _final_checks(d, cap, typed=True)           # fresh look right before the irreversible tap
    if r is None:
        return False, "NOT shared: could not verify the final screen (AI unavailable)"
    if not (r["final_screen"] and r["is_video"] and r["caption_present"]):
        _remember("Last check before Share failed (%s)." % r.get("seen", "?"), "Never tap Share unless the final screen shows the caption and a video.")
        return False, "NOT shared: last check failed: " + str(r.get("seen"))
    if agent._step_note():                          # a note arrived during the check itself
        return False, "NOT shared: you sent a new note just now"
    print("  [guard] keyboard closed and everything checked -> Share")
    if not _tap_share(d):
        return False, "NOT shared: the Share button was not found"
    # ---- from here on the Reel may be posted ----
    retried = False
    for wait in (4, 5, 8, 15):
        time.sleep(wait)
        state, why = _share_state(d)
        print("  [guard] after Share: %s %s" % (state, why))
        if state == "started":
            return True, "Reel is being shared (" + why + ")"
        if state == "error":
            return True, "Share was tapped but Instagram shows a problem (%s) - open Instagram and check before posting again" % why
        if state == "still" and not retried and wait >= 5:
            # the same final screen is still there after ~9 s: the first tap did not register. Close the keyboard again and tap once more.
            retried = True
            print("  [guard] the final screen is still showing -> the tap did not register; closing the keyboard and tapping Share once more")
            _close_keyboard(d)
            if _composer_visible(d) and _tap_share(d, strict=True):
                print("  [guard] second Share tap done")
            else:
                print("  [guard] composer not confirmed -> NOT tapping again (the reel may already be posted)")
    res = look(d, {"uploading_or_done": "Does the screen show that a Reel is being shared or was shared (e.g. 'Sharing to Reels' progress bar, "
                                        "'Your reel was shared', or the new reel uploading on the profile/feed)?"})
    if res and res["uploading_or_done"]:
        return True, "Reel is being shared: " + str(res.get("seen"))
    return True, "Share was tapped but the upload could NOT be confirmed - open Instagram and check before posting again"


def post_reel(d, cap, video_name=""):
    """Returns (ok, why). ok=True means the Reel was shared (or Share was tapped and could not be confirmed)."""
    keep = list(agent.FORBIDDEN)
    agent.FORBIDDEN[:] = ["share"]                  # the AI can NEVER tap Share; only _share() does, after all checks
    last = "not started"
    try:
        for rnd in range(1, MAX_ROUNDS + 1):
            print("\n[guard] round %d/%d" % (rnd, MAX_ROUNDS))
            STATE.update(video_tapped=False, how="")
            if rnd > 1:
                _restart_instagram(d)
            for name, fn in (("open", lambda: _open_picker(d)), ("pick", lambda: _pick_video(d)), ("caption", lambda: _caption_screen(d, cap))):
                ok, last = fn()
                print("[guard] %s: %s - %s" % (name, "OK" if ok else "FAIL", last))
                if not ok:
                    break
            else:
                ok, last = _share(d, cap)
                print("[guard] share: %s - %s" % ("OK" if ok else "FAIL", last))
                if ok:
                    return True, last
                # "NOT shared" means nothing was posted, so another round is safe
        return False, "NOT posted after %d rounds. Last problem: %s" % (MAX_ROUNDS, last)
    finally:
        agent.FORBIDDEN[:] = keep
