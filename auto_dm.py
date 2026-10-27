"""auto_dm.py - links the Meesho Creator Club auto-DM to the Reel that was just posted.

Steps (each one is verified before the next one starts; code taps first, the AI only points when the screen tree has no match):
   1. kill Meesho, open the product link, make sure the product is wishlisted FRESH (already wishlisted -> un-heart, then heart again,
      so it is the newest item at the TOP of the wishlist)
   2. restart the Meesho app
   3. Profile icon (top left)
   4. "Open" button (top right, same row as "Meesho Creator Club")
   5. "Send Products to DM"
   6. select the most recent Instagram post
   7. "Continue"
   8. "Select from your wishlist"
   9. select the recently wishlisted product (top of the list)
  10. "Done"
  11. "Complete"   (tapped ONCE, never retried)

Used from main_link.py:   ok, why = auto_dm.link_auto_dm(d, url, product_name)
Test on its own:          python auto_dm.py "PRODUCT_LINK" "product name"
"""
import json, os, sys, time
from google.genai import types
import agent, llm, phone
from config import PKG

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")
STRICT_NAME = os.getenv("AUTODM_STRICT", "0").strip() == "1"     # 1 = stop if the picked product does not look like the right one
# The AI helper that clears popups must never tap these.
NEVER_TAP = ["complete", "buy", "order", "pay", "place order", "delete", "remove", "log out", "logout", "sign out"]


# ------------------------------------------------------------ memory between runs ----
# Where a tap WORKED last time (fractions of the screen), per step. A guess by the AI pointer or the fixed place is saved only after the
# next screen really appeared. The next run tries that place BEFORE asking the AI again. If it stops working, it is forgotten.
MEM_FILE = os.path.join(BASE, "memory", "autodm_positions.json")


def _mem_load():
    try:
        with open(MEM_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _mem_get(name):
    v = _mem_load().get(name)
    try:
        return (float(v[0]), float(v[1])) if v else None
    except Exception:
        return None


def _mem_set(name, fx, fy):
    try:
        m = _mem_load()
        m[name] = [round(fx, 4), round(fy, 4)]
        os.makedirs(os.path.dirname(MEM_FILE), exist_ok=True)
        with open(MEM_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
    except Exception as e:
        print("  [autodm] could not save the memory:", str(e)[:80])


def _mem_del(name):
    try:
        m = _mem_load()
        if name in m:
            del m[name]
            with open(MEM_FILE, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=1)
    except Exception:
        pass


# ----------------------------------------------------------------- screen reading ----
def _nodes(d):
    try:
        W, H = agent._screen(d)
        return agent._ui_nodes(d), W, H
    except Exception:
        return [], 1080, 2400


def _norm(s):
    return (s or "").strip().lower().replace("\u2019", "'")


def _blob(d):
    nodes, _, _ = _nodes(d)
    return " | ".join((_norm(n.get("text")) + " " + _norm(n.get("desc"))).strip() for n in nodes)


def _wait_for(d, patterns, timeout=12):
    """Poll the screen tree until one of the (lowercase) patterns is on screen."""
    end = time.time() + timeout
    while True:
        b = _blob(d)
        if any(p in b for p in patterns):
            return True
        if time.time() >= end:
            return False
        time.sleep(1.5)


def _find(nodes, W, H, exact=None, contains=None, rid=None, region=None):
    """Elements matching by exact text/description, part of it, or part of the resource id.
    region = (x1, y1, x2, y2) as fractions of the screen; the CENTRE of the element must be inside."""
    out = []
    for n in nodes:
        t, ds, i = _norm(n.get("text")), _norm(n.get("desc")), _norm(n.get("id"))
        ok = bool(exact and (t in exact or ds in exact))
        ok = ok or bool(contains and any(c in t or c in ds for c in contains))
        ok = ok or bool(rid and any(r in i for r in rid))
        if not ok:
            continue
        x1, y1, x2, y2 = n["b"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if region and not (region[0] * W <= cx <= region[2] * W and region[1] * H <= cy <= region[3] * H):
            continue
        out.append(n)
    out.sort(key=lambda n: (n["b"][1], -n["b"][2]))
    return out


def _centre(n):
    return (n["b"][0] + n["b"][2]) / 2, (n["b"][1] + n["b"][3]) / 2


def _tap(d, cx, cy, W, H):
    phone.tap_norm(d, int(round(cx * 1000 / W)), int(round(cy * 1000 / H)))
    time.sleep(2)


def _snap(d, name):
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, "autodm_%s_%s.png" % (name.replace(" ", "_")[:30], time.strftime("%H%M%S"))), "wb") as f:
            f.write(phone.screenshot(d))
    except Exception:
        pass


# --------------------------------------------------------------------- AI eyes ----
def _image(d, width=720):
    return types.Part.from_bytes(data=agent.shrink(phone.screenshot(d), width), mime_type="image/jpeg")


def _ask(d, questions):
    """Yes/no questions about the CURRENT screen. Returns {key: bool, 'seen': str} or None when the AI could not answer."""
    keys = list(questions)
    prompt = ("This is a screenshot of the Meesho shopping app on an Android phone.\n"
              "Answer every question with true or false. Look carefully, do not guess: if you cannot see it, answer false.\n"
              + "\n".join("- %s: %s" % (k, questions[k]) for k in keys) +
              "\nReturn JSON: {" + ", ".join('"%s": true/false' % k for k in keys) + ', "seen": "one short sentence: what the screen shows"}')

    def validate(t):
        try:
            data = json.loads(t)
        except Exception:
            return "not valid JSON"
        for k in keys:
            if not isinstance(data.get(k), bool):
                return "'%s' must be true or false" % k
        return None

    for _ in range(3):
        try:
            return json.loads(llm.generate([_image(d), prompt], validate=validate))
        except Exception as e:
            print("  [autodm] AI check could not run (%s) -> trying again in 10 s" % str(e)[:90])
            time.sleep(10)
    return None


def _point(d, what, ymin=0, ymax=1000):
    """The AI points at something (normalized 0-1000). Only accepted when it is inside the allowed vertical band."""
    def validate(t):
        try:
            data = json.loads(t)
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
            data = json.loads(llm.generate([_image(d, 900),
                "Screenshot of the Meesho shopping app on an Android phone. Find: " + what + ". "
                "Coordinates are normalized 0-1000, top-left origin, centre of the target. If you cannot see it, found is false. "
                'Return JSON: {"found": true/false, "x": 0-1000, "y": 0-1000}'], validate=validate))
            if data["found"] and ymin <= float(data["y"]) <= ymax:
                return int(round(float(data["x"]))), int(round(float(data["y"])))
            return None
        except Exception as e:
            print("  [autodm] AI pointer could not run (%s)" % str(e)[:90])
            time.sleep(5)
    return None


def _clear_popups(d, step):
    """The AI closes a popup / permission / tip that is in the way. It may NOT tap Complete, buy, delete, etc."""
    old = list(getattr(agent, "FORBIDDEN", []))
    agent.FORBIDDEN[:] = NEVER_TAP
    try:
        agent.run_task(d,
            "A bot is linking an auto-DM in the Meesho app (step: '" + step + "'). The screen is not as expected. "
            "ONLY close what is blocking: a popup, tip, rating, permission or offer dialog (tap Close / X / Skip / Not now / Allow / OK). "
            "Do NOT press Back unless the screen is clearly a wrong screen, and do NOT buy, order, pay, delete, remove or log out. "
            "If nothing is blocking, reply done immediately.", max_steps=4, key=None)
    except Exception as e:
        print("  [autodm] popup helper error:", str(e)[:100])
    finally:
        agent.FORBIDDEN[:] = old


def _profile_icon(nodes, W, H):
    """The profile icon on the Meesho home screen: a small square element in the top-left corner (it has no text)."""
    c = []
    for n in nodes:
        x1, y1, x2, y2 = n["b"]
        w, h = x2 - x1, y2 - y1
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if 0.04 * W <= w <= 0.2 * W and 0.04 * W <= h <= 0.2 * W and 0.6 <= w / max(h, 1) <= 1.6 \
                and cx <= 0.25 * W and 0.02 * H <= cy <= 0.13 * H:
            c.append(n)
    c.sort(key=lambda n: (n["b"][0], n["b"][1]))
    return c[0] if c else None


def _goal_reached(d, goal):
    """Judge a step by LOOKING at the screen (bottom sheets, dialogs and web content are often missing from the screen tree).
    True / False, or None when the AI could not answer."""
    r = _ask(d, {"reached": "A bot is doing this step in the Meesho app: " + goal + "\nIs the goal of this step ALREADY reached on the CURRENT screen? "
                            "Look at the whole screenshot, including bottom sheets and dialogs."})
    return None if r is None else bool(r["reached"])


def _rescue(d, name, goal, expect=None, allow=()):
    """LAST RESORT before a step is called failed: the AI drives the phone like a person to finish THIS step.
    allow = words from NEVER_TAP that this particular step may tap (only 'complete' for the last step)."""
    print("  [autodm] %s: the program could not do it -> asking the AI to do it..." % name)
    old = list(getattr(agent, "FORBIDDEN", []))
    agent.FORBIDDEN[:] = [w for w in NEVER_TAP if w not in allow]
    ok = False
    try:
        ok, why = agent.run_task(d,
            "A bot is linking the Meesho Creator Club auto-DM in the Meesho app. Current step: '" + name + "'. " + goal +
            " Close popups that are in the way. Never buy, order, pay, delete, remove or log out. Reply done when the goal is reached.",
            max_steps=8, key=None)
        print("  [autodm] AI helper:", "reached the goal" if ok else "did not reach the goal", "-", str(why)[:100])
    except Exception as e:
        print("  [autodm] AI helper error:", str(e)[:100])
    finally:
        agent.FORBIDDEN[:] = old
    if expect and _wait_for(d, expect, 4):
        return True
    g = _goal_reached(d, goal)                       # the tree can be blind: believe what the screenshot shows
    if g is not None:
        return g
    return bool(ok)


# ------------------------------------------------------------------ tap primitive ----
def _click(d, name, exact=None, contains=None, rid=None, region=None, ai=None, ai_band=(0, 1000),
           expect=None, timeout=12, tries=3, row_of=None, rescue=None, allow=(), rescue_after_tap=True,
           shape=None, fixed=None):
    """Tap one thing: screen tree first, then the AI pointer. Then wait until `expect` (lowercase substrings) is on screen.
    row_of = text of another element: when several match, take the one on the same row as that element."""
    tapped = False
    learned = _mem_get(name)                             # where this tap worked in an earlier run
    last = None                                          # (fx, fy, kind) of the last guessed tap, saved once it is proven
    for attempt in range(1, tries + 1):
        if attempt > 1 and ((expect and _wait_for(d, expect, 1)) or (rescue and _goal_reached(d, rescue))):
            if last and last[2] != "learned":
                _mem_set(name, last[0], last[1])
            return True                                  # the earlier tap worked, the screen was only slow
        nodes, W, H = _nodes(d)
        hits = _find(nodes, W, H, exact=exact, contains=contains, rid=rid, region=region)
        if not hits and shape:                           # an icon without any text: found by its size and place in the tree
            n = shape(nodes, W, H)
            if n:
                hits = [n]
        if hits and row_of:
            ref = _find(nodes, W, H, contains=[row_of])
            if ref:
                ry = _centre(ref[0])[1]
                hits.sort(key=lambda n: abs(_centre(n)[1] - ry))
        if hits:
            cx, cy = _centre(hits[0])
            print("  [autodm] %s: tapping (found in the screen tree) at (%d, %d)" % (name, cx, cy))
            _tap(d, cx, cy, W, H)
            tapped = True
        elif learned and attempt == 1:                   # worked in an earlier run: try it before asking the AI again
            cx, cy = learned[0] * W, learned[1] * H
            print("  [autodm] %s: tapping the place that worked last time (%d, %d)" % (name, cx, cy))
            _tap(d, cx, cy, W, H)
            tapped = True
            last = (learned[0], learned[1], "learned")
        elif fixed and attempt % 2 == 1:                 # known place on this phone (fractions of the screen), no AI needed
            cx, cy = fixed[0] * W, fixed[1] * H
            print("  [autodm] %s: tapping the known place (%d, %d)" % (name, cx, cy))
            _tap(d, cx, cy, W, H)
            tapped = True
            last = (fixed[0], fixed[1], "fixed")
        elif ai:
            pt = _point(d, ai, *ai_band)
            if not pt:
                print("  [autodm] %s: not on screen (try %d)" % (name, attempt))
                _clear_popups(d, name)
                continue
            print("  [autodm] %s: the AI pointed at it: %s -> tapping" % (name, pt))
            phone.tap_norm(d, pt[0], pt[1])
            tapped = True
            last = (pt[0] / 1000.0, pt[1] / 1000.0, "ai")
            time.sleep(2)
        else:
            print("  [autodm] %s: not on screen (try %d)" % (name, attempt))
            _clear_popups(d, name)
            continue
        screen_ok = (not expect) or _wait_for(d, expect, timeout)
        if not screen_ok and rescue and _goal_reached(d, rescue):
            print("  [autodm] %s: the screen tree shows nothing new, but the screenshot shows the goal is reached" % name)
            screen_ok = True
        if screen_ok:
            if last and expect and last[2] != "learned":
                _mem_set(name, last[0], last[1])         # proven: the next screen appeared -> remember it for the next run
                print("  [autodm] %s: remembered this place for the next run" % name)
            return True
        print("  [autodm] %s: the next screen did not appear (try %d)" % (name, attempt))
        if last and last[2] == "learned":
            print("  [autodm] %s: the remembered place no longer works -> forgetting it" % name)
            _mem_del(name)
            learned = None
        _clear_popups(d, name)
    if rescue and (rescue_after_tap or not tapped):
        if _rescue(d, name, rescue, expect, allow):
            return True
    _snap(d, name)
    return False


# ------------------------------------------------------------------------ step 1 ----
def _open_link(d, url):
    for params in ({"url": url, "package": PKG["meesho"]}, {"url": url}):
        try:
            d.execute_script("mobile: deepLink", params)
            return True
        except Exception as e:
            print("  [autodm] deepLink try failed:", str(e)[:100])
    return False


# The product page has TWO hearts: the real one (a heart with the text "Wishlist" / "Wishlisted" on the product itself) and the
# one in the top bar next to Search and Cart, which only OPENS the wishlist page. Only the labelled one may be tapped.
NAV_BAR = 0.12           # everything above this fraction of the screen height is the top bar: never tap there


def _wish_buttons(nodes, W, H):
    """Elements whose text/description is exactly 'Wishlist' or 'Wishlisted', outside the top bar. Returns [(node, is_wishlisted)]."""
    out = []
    for n in nodes:
        t, ds = _norm(n.get("text")), _norm(n.get("desc"))
        for lab in (t, ds):
            if lab in ("wishlist", "wishlisted", "add to wishlist", "remove from wishlist", "wishlisted!"):
                cx, cy = _centre(n)
                if cy > NAV_BAR * H:
                    out.append((n, lab in ("wishlisted", "remove from wishlist", "wishlisted!")))
                break
    out.sort(key=lambda x: x[0]["b"][1])
    return out


def _heart_state(d):
    """True = wishlisted, False = not, None = could not tell. The text on the button says it: 'Wishlisted' / 'Wishlist'."""
    nodes, W, H = _nodes(d)
    btns = _wish_buttons(nodes, W, H)
    if btns:
        return btns[0][1]
    r = _ask(d, {"filled": "Look at the heart icon that has the text 'Wishlist' or 'Wishlisted' on the product (NOT the heart in the top bar "
                           "next to Search and Cart). Is it FILLED (solid red/pink, text 'Wishlisted') = already in the wishlist? "
                           "An empty outline heart with the text 'Wishlist' = false."})
    return None if r is None else bool(r["filled"])


def _on_product_page(d):
    r = _ask(d, {"product_page": "Is this a product detail page (product photos, price, Add to cart / Buy now)? "
                                 "A list of wishlisted products or a wishlist page = false."})
    return bool(r and r["product_page"])


def _tap_heart(d):
    """Tap the heart with the 'Wishlist' / 'Wishlisted' label. Never the heart in the top bar."""
    nodes, W, H = _nodes(d)
    btns = _wish_buttons(nodes, W, H)
    if not btns:                          # the label may be below the fold of the first screen: nudge once
        try:
            phone.swipe(d, "up")
            time.sleep(1.5)
            nodes, W, H = _nodes(d)
            btns = _wish_buttons(nodes, W, H)
        except Exception:
            pass
    if btns:
        cx, cy = _centre(btns[0][0])
        print("  [autodm] tapping the heart with the text '%s' at (%d, %d)" % (_norm(btns[0][0].get("text")) or _norm(btns[0][0].get("desc")), cx, cy))
        _tap(d, cx, cy, W, H)
    else:
        pt = _point(d, "the HEART icon that has the text 'Wishlist' or 'Wishlisted' next to it, on the product itself (below or on the product photos). "
                       "NOT the heart in the top bar next to the Search and Cart icons", int(NAV_BAR * 1000) + 10, 900)
        if not pt:
            return False
        print("  [autodm] the AI pointed at the labelled heart: %s -> tapping" % (pt,))
        phone.tap_norm(d, pt[0], pt[1])
        time.sleep(2)
    if not _on_product_page(d):           # we landed on the wishlist page: wrong heart, go back
        print("  [autodm] this opened another page (the wrong heart?) -> going Back")
        phone.adb("shell", "input", "keyevent", "4")
        time.sleep(2)
        return False
    return True


def _set_heart(d, want):
    for _ in range(2):
        if not _tap_heart(d):
            return False
        time.sleep(1.5)
        if _heart_state(d) == want:
            return True
    return False


def wishlist_fresh(d, url):
    """Open the product and make it the NEWEST wishlist item: if it is already wishlisted, remove it first, then add it again."""
    for attempt in (1, 2):
        # kill Meesho first so the link opens on a clean app and does not keep the previous screen / state
        phone.adb("shell", "am", "force-stop", PKG["meesho"])
        time.sleep(2)
        if not _open_link(d, url):
            return False, "the product link did not open"
        time.sleep(8)
        r = _ask(d, {"product_page": "Is this a product detail page (product photos, price, a heart icon, Add to cart / Buy now)?"})
        if r and r["product_page"]:
            break
        print("  [autodm] this is not the product page (%s)" % (r or {}).get("seen", "?"))
        _clear_popups(d, "open product")
    else:
        return False, "the product page did not open"
    st = _heart_state(d)
    if st is None:
        return False, "could not tell if the heart is filled"
    if st:
        print("  [autodm] already wishlisted -> removing it first, then adding it again (so it is on top)")
        if not _set_heart(d, False):
            return False, "could not remove the product from the wishlist"
        time.sleep(1.5)
    if not _set_heart(d, True):
        return False, "could not wishlist the product"
    return True, "product wishlisted fresh"


# ------------------------------------------------------------ select the post ----
def _post_tiles(nodes, W, H):
    """Square-ish thumbnails of the post grid, found by size in the screen tree. Newest = first (top-left)."""
    out = []
    for n in nodes:
        x1, y1, x2, y2 = n["b"]
        w, h = x2 - x1, y2 - y1
        if 0.25 * W <= w <= 0.42 * W and 0.8 <= h / max(w, 1) <= 1.25 and y1 >= 0.18 * H:
            out.append(n)
    out.sort(key=lambda n: (round(n["b"][1] / (0.05 * H)), n["b"][0]))
    return out


def _post_selected(d):
    """Selected = the Continue button shows up (it is NOT there before a post is ticked) OR the AI sees a ticked circle."""
    if _wait_for(d, ["continue"], 3):
        return True
    r = _ask(d, {"selected": "This is a grid of posts, each tile with a small round selection circle in its top-left corner. "
                             "Is any ONE circle filled / ticked / checked (selected)? All circles empty = false."})
    return bool(r and r["selected"])


def select_recent_post(d):
    """Tick the circle of the FIRST (most recent) post. Code first, AI pointer second, the AI driving the phone as the last resort."""
    for attempt in (1, 2):
        if _post_selected(d):
            return True
        nodes, W, H = _nodes(d)
        tiles = _post_tiles(nodes, W, H)
        targets = []
        if tiles:
            x1, y1, x2, y2 = tiles[0]["b"]
            w = x2 - x1
            targets.append(("the circle of the first post", x1 + 0.09 * w, y1 + 0.075 * w))
            targets.append(("the middle of the first post", (x1 + x2) / 2, (y1 + y2) / 2))
        for label, cx, cy in targets:
            print("  [autodm] post: tapping %s at (%d, %d)" % (label, cx, cy))
            _tap(d, cx, cy, W, H)
            if _post_selected(d):
                return True
        pt = _point(d, "the small empty ROUND SELECTION CIRCLE in the top-left corner of the FIRST (top-left) post tile in the grid of posts "
                       "(not the screen title, not a back arrow)", 150, 900)
        if pt:
            print("  [autodm] post: the AI pointed at the first post's circle: %s -> tapping" % (pt,))
            phone.tap_norm(d, pt[0], pt[1])
            time.sleep(2)
            if _post_selected(d):
                return True
        _clear_popups(d, "select post")
    return _rescue(d, "select post",
                   "You are on the 'Select Post' screen (a grid of Instagram posts, each with a small round circle in its top-left corner). "
                   "Select the FIRST (top-left, most recent) post: tap its round circle (or the post) ONCE so the circle becomes filled/ticked. "
                   "Select only ONE post. Done when one post is ticked and a Continue button appears.",
                   expect=["continue"])


# ------------------------------------------------------- the WISHLIST bottom sheet ----
# The sheet is NOT in the screen tree (the tree still shows the page behind it), so it is judged by LOOKING at the screen.
# Layout seen on the phone (fractions of the screen): first product checkbox (0.067, 0.313), first card (0.261, 0.400), Done (0.731, 0.942).
SHEET_Q = {"sheet": "Is a bottom sheet titled 'WISHLIST' open: a dark panel over the lower part of the screen with a grid of wishlisted product "
                    "cards (each with a small checkbox in its top-left corner) and, at the bottom, a '... Selected' counter and a Done button?"}


def _sheet_state(d):
    """True = the wishlist sheet is open, False = it is not, None = the AI could not answer."""
    r = _ask(d, SHEET_Q)
    return None if r is None else bool(r["sheet"])


def _try_taps(d, name, cands, check):
    """Tap the candidates one after the other (remembered place first). After each tap check() says True / False / None (unknown = trust the tap).
    Stops at the first tap that worked, so one thing is never toggled twice."""
    nodes, W, H = _nodes(d)
    learned = _mem_get(name)
    if learned:
        cands = [("the place that worked last time", learned[0], learned[1])] + list(cands)
    for label, fx, fy in cands:
        print("  [autodm] %s: tapping %s at (%d, %d)" % (name, label, fx * W, fy * H))
        _tap(d, fx * W, fy * H, W, H)
        ok = check()
        if ok is None or ok:
            if not label.startswith("the place that worked"):
                _mem_set(name, fx, fy)
            return True
        if label.startswith("the place that worked"):
            _mem_del(name)
    return False


def open_wishlist_sheet(d):
    """Tap 'Select from your wishlist' and wait until the sheet is REALLY open. Never taps again while the sheet is open."""
    for attempt in (1, 2, 3):
        if _sheet_state(d):
            return True
        nodes, W, H = _nodes(d)
        hits = _find(nodes, W, H, contains=["select from your wishlist", "select from wishlist"])
        if hits:
            cx, cy = _centre(hits[0])
            print("  [autodm] Select from your wishlist: tapping (found in the screen tree) at (%d, %d)" % (cx, cy))
            _tap(d, cx, cy, W, H)
        else:
            pt = _point(d, "the 'Select from your wishlist' option in the middle of the screen", 150, 900)
            if not pt:
                _clear_popups(d, "select from your wishlist")
                continue
            print("  [autodm] Select from your wishlist: the AI pointed at it: %s -> tapping" % (pt,))
            phone.tap_norm(d, pt[0], pt[1])
            time.sleep(2)
        for _ in range(4):                                  # the sheet slides up: look a few times before tapping anything again
            time.sleep(2)
            if _sheet_state(d):
                return True
    _rescue(d, "select from your wishlist",
            "Tap the 'Select from your wishlist' option once. Done when a bottom sheet titled WISHLIST with product cards and a Done button is open. "
            "Do NOT tap any product.")
    return bool(_sheet_state(d))


def _one_selected(d, product_name=""):
    """True when the counter at the bottom of the sheet says '1 Selected'; None when the AI could not answer."""
    time.sleep(1)
    qs = {"one": "Does the counter at the bottom-left of the screen say '1 Selected' (the number 1 followed by Selected)? '0 Selected' = false."}
    r = _ask(d, qs)
    return None if r is None else bool(r["one"])


def select_first_product(d, product_name=""):
    """Tick the FIRST product of the wishlist sheet (the one that was just wishlisted is at the top)."""
    if _one_selected(d) is True:
        return True                                          # already ticked: never tap again (it would untick)
    cands = [("the first product's checkbox", 0.067, 0.313), ("the middle of the first product's card", 0.261, 0.400)]
    if _try_taps(d, "select product", cands, lambda: _one_selected(d)):
        pass
    else:
        pt = _point(d, "the small CHECKBOX in the top-left corner of the FIRST (top-left) product card in the WISHLIST sheet", 250, 900)
        if pt:
            print("  [autodm] select product: the AI pointed at the first checkbox: %s -> tapping" % (pt,))
            phone.tap_norm(d, pt[0], pt[1])
            time.sleep(2)
        if _one_selected(d) is False:
            _rescue(d, "select product",
                    "A bottom sheet titled WISHLIST shows product cards, each with a small checkbox at its top-left. Select the FIRST (top-left) product: "
                    "tap its checkbox ONCE so the counter at the bottom says '1 Selected'. Select only ONE product. Do NOT tap Done.")
            if _one_selected(d) is False:
                return False
    if product_name:
        r = _ask(d, {"right": "Does the FIRST product card (top-left) look like this product: '%s'? (a similar kind of item is enough)" % product_name})
        if r is not None and not r["right"]:
            msg = "the top wishlist product may not be the right one (%s)" % r.get("seen", "?")
            print("  [autodm] WARNING: " + msg)
            if STRICT_NAME:
                return False
    return True


def _sheet_closed(d):
    """True = the sheet closed after Done, False = still open, None = unknown."""
    time.sleep(2)
    st = _sheet_state(d)
    return None if st is None else (not st)


def press_done(d):
    """Tap Done once the product is ticked and make sure the sheet closed. Never taps while the sheet is already closed."""
    for attempt in (1, 2):
        if _sheet_state(d) is False:
            return True
        nodes, W, H = _nodes(d)
        cands = []
        hit = _find(nodes, W, H, exact=["done"], region=(0.4, 0.8, 1.0, 1.0))
        if hit:
            cx, cy = _centre(hit[0])
            cands.append(("Done (found in the screen tree)", cx / W, cy / H))
        cands.append(("the Done button", 0.731, 0.942))
        if _try_taps(d, "done", cands, lambda: _sheet_closed(d)):
            return True
    return bool(_rescue(d, "done", "Tap the Done button at the bottom right of the WISHLIST sheet once. Done when the sheet is closed."))


# ------------------------------------------------------------------------- main ----
def link_auto_dm(d, url, product_name=""):
    """Returns (ok, why). ok=True also when Complete was tapped but the final screen could not be confirmed."""
    def fail(msg):
        print("[autodm] FAIL: " + msg)
        _snap(d, "fail")
        return False, msg

    print("[autodm] 1/11 kill Meesho, open the product link, fresh wishlist")
    ok, why = wishlist_fresh(d, url)
    if not ok:
        return fail(why)

    print("[autodm] 2/11 restarting Meesho")
    phone.adb("shell", "am", "force-stop", PKG["meesho"])
    time.sleep(2)
    phone.open_app(d, PKG["meesho"])
    time.sleep(8)

    print("[autodm] 3/11 Profile")
    if not _click(d, "Profile", contains=["profile"], region=(0.0, 0.0, 0.4, 0.2),
                  shape=_profile_icon, fixed=(0.05, 0.07),
                  ai="the PROFILE icon at the top left of the home screen", ai_band=(0, 250),
                  expect=["creator club"], timeout=15,
                  rescue="Open the Profile page: tap the profile icon at the top-left of the Meesho home screen. Done when 'Meesho Creator Club' is visible."):
        return fail("could not open Profile / did not see Meesho Creator Club")

    print("[autodm] 4/11 Open (Meesho Creator Club)")
    if not _click(d, "Open", exact=["open"], region=(0.45, 0.0, 1.0, 0.7), row_of="creator club",
                  ai="the 'Open' button at the right end of the 'Meesho Creator Club' row", ai_band=(0, 700),
                  expect=["send products to dm", "send product"], timeout=15,
                  rescue="Tap the 'Open' button at the right end of the 'Meesho Creator Club' row. Done when the Creator Club page with 'Send Products to DM' is visible."):
        return fail("could not open the Creator Club")

    print("[autodm] 5/11 Send Products to DM")
    # region starts below the header: the next screen has a TITLE with the same words, which must never be tapped as a button
    if not _click(d, "Send Products to DM", contains=["send products to dm"], region=(0.0, 0.2, 1.0, 1.0),
                  ai="the 'Send Products to DM' option card (not a screen title)", ai_band=(200, 1000),
                  expect=["select post", "select a post", "choose post", "continue", "recent post", "instagram post"], timeout=15,
                  rescue="Tap the 'Send Products to DM' option (the card, not a screen title). Done when the 'Select Post' screen with a grid of posts is visible."):
        return fail("could not open 'Send Products to DM'")

    print("[autodm] 6/11 select the recent Instagram post")
    _wait_for(d, ["select post", "select a post"], 10)     # the posts load a moment after the screen
    time.sleep(3)
    if not select_recent_post(d):
        return fail("could not select the recent Instagram post")

    print("[autodm] 7/11 Continue")
    if not _click(d, "Continue", exact=["continue"], ai="the 'Continue' button", ai_band=(500, 1000),
                  expect=["select from your wishlist", "your wishlist"], timeout=15,
                  rescue="Tap the Continue button at the bottom of the 'Select Post' screen. If it is missing, first tick the first post's circle. "
                         "Done when the screen offers 'Select from your wishlist'."):
        return fail("could not press Continue")

    print("[autodm] 8/11 Select from your wishlist")
    if not open_wishlist_sheet(d):
        return fail("could not open the wishlist picker")

    print("[autodm] 9/11 select the recently wishlisted product (top of the list)")
    if not select_first_product(d, product_name):
        return fail("could not select the wishlisted product")

    print("[autodm] 10/11 Done")
    if not press_done(d):
        return fail("could not press Done")
    _wait_for(d, ["complete"], 8)

    print("[autodm] 11/11 Complete")
    if not _click(d, "Complete", exact=["complete"], ai="the 'Complete' button", ai_band=(400, 1000), tries=1,
                  rescue="Tap the 'Complete' button ONCE.", allow=("complete",), rescue_after_tap=False):
        return fail("could not press Complete")
    # from here on the auto-DM may be live: never tap Complete again
    time.sleep(5)
    r = _ask(d, {"done": "Does the screen show that the auto-DM was set up / is active / saved (a success message, a confirmation, "
                         "or the Creator Club page again with the post linked)? The 'Complete' button still showing = false."})
    if r and r["done"]:
        return True, "auto-DM linked: " + str(r.get("seen"))
    return True, "Complete was tapped but the result could NOT be confirmed - open Meesho Creator Club and check"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Use: python auto_dm.py "PRODUCT_LINK" ["product name"]')
        sys.exit(1)
    drv = phone.connect()
    try:
        okk, whyy = link_auto_dm(drv, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
        print("auto-dm:", "OK" if okk else "FAIL", "-", whyy)
    finally:
        try:
            drv.quit()
        except Exception:
            pass
