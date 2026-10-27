"""Link -> video + caption. By default it does NOT post anything. main.py is untouched.
Use: python main_link.py "PRODUCT_LINK"           (make video + caption only, you post by hand)
     python main_link.py "PRODUCT_LINK" --prompt-only (only the YT Create prompt + Instagram caption, no video)
     python main_link.py "PRODUCT_LINK" --post    (also posts the Reel on Instagram + links Creator Club auto-DM,
                                                   ONLY if every video check passed)
"""
import io, os, re, sys, json, time
import phone, agent, llm, post_guard, auto_dm, find_product, yt_flow, script_writer, verify_video, supervisor, strict_checks, image_facts, factcheck
from PIL import Image, ImageDraw, ImageFont
from google.genai import types
from config import PKG, SHORT_PROMPT_STYLE

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(BASE, "outputs_test")
LOGS = os.path.join(BASE, "logs")
STYLE = (SHORT_PROMPT_STYLE or "").strip().strip('"').strip("'")
if STYLE.upper().startswith("SHORT_PROMPT_STYLE="):      # .env/config line was read with its name included
    STYLE = STYLE.split("=", 1)[1].strip()

# Where the product page is read: web = in a browser on the PC (the phone stays free), phone = in the Meesho app (old way),
# auto = the PC browser first, and the phone only if that fails. Set SCRAPE in .env; the control page uses web for "prompt + caption" mode.
SCRAPE = (os.getenv("SCRAPE", "") or "phone").strip().lower()
YT_PKG = os.getenv("YTCREATE_PKG", "com.google.android.apps.youtube.producer")   # YouTube Create app
VID_FOLDERS = ["/sdcard/Movies", "/sdcard/DCIM/Camera", "/sdcard/Download", "/sdcard/DCIM", "/sdcard/Pictures"]


def step(name, res):
    ok, why = res
    print("[" + ("OK" if ok else "FAIL") + "] " + name + ": " + str(why))
    if not ok:
        raise RuntimeError(name + " failed: " + str(why))


def clean_desc(t):
    t = re.sub(r"(?:\u00c2)?[\u20b9\u00a5]\s?\d[\d,]*|Rs\.?\s?\d[\d,]*", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def open_link(d, url):
    for params in ({"url": url, "package": PKG["meesho"]}, {"url": url}):
        try:
            d.execute_script("mobile: deepLink", params)
            return True
        except Exception as e:
            print("  deepLink try failed:", str(e)[:100])
    return False


_UNIT_WORDS = {"cm", "mm", "m", "kg", "gm", "g", "ml", "l", "ltr", "pc", "pcs", "in", "w", "v", "mah", "gb"}


def screen_texts(d):
    """All visible text on the current screen (free, from Appium, no AI)."""
    out = []
    try:
        for n in agent._ui_nodes(d):
            for v in (n.get("text"), n.get("desc")):
                v = (v or "").strip()
                if len(v) > 2 or v.lower() in _UNIT_WORDS:      # "Cm" / "Kg" / "Ml" are real spec values, keep them
                    out.append(v)
    except Exception:
        pass
    return out


GALLERY_BOX = {"box": None}     # pixel box (x1, y1, x2, y2) of the big product picture, found once on the first screenshot


def crop_product(im):
    """Crop a screenshot (PIL image) to the product picture. Uses the real position found on screen;
    only if that is unknown it falls back to the old fixed area (which misses the picture when a page has banners on top)."""
    if GALLERY_BOX.get("full"):          # pictures downloaded from the web page: already only the product, nothing to crop
        return im
    w, h = im.size
    b = GALLERY_BOX.get("box")
    if b:
        x1, y1, x2, y2 = [max(0, int(v)) for v in b]
        x2, y2 = min(x2, w), min(y2, h)
        if x2 - x1 > 80 and y2 - y1 > 80:
            return im.crop((x1, y1, x2, y2))
    return im.crop((0, int(.08 * h), w, int(.55 * h)))


def find_gallery_box(d, W, H):
    """The big product picture from the app's own UI tree (no AI): the largest, roughly square image in the upper part of the screen."""
    best = None
    try:
        for n in yt_flow.nodes(d):
            x1, y1, x2, y2 = n["b"]
            w, h = x2 - x1, y2 - y1
            if "Image" not in (n.get("cls") or ""):
                continue
            if w < .5 * W or h < .15 * H or h > .65 * H or y1 > .6 * H or x2 > W + 5 or y2 > H + 5:
                continue
            if not (.6 <= h / float(w) <= 1.5):
                continue
            if best is None or w * h > (best[2] - best[0]) * (best[3] - best[1]):
                best = (x1, y1, x2, y2)
    except Exception:
        pass
    return best


def ai_gallery_box(png):
    """Fallback when the UI tree does not show the picture: ask the AI where the main product photo is. Returns a pixel box or None."""
    im = Image.open(io.BytesIO(png))
    W, H = im.size
    prompt = (
        "This is a screenshot of a product page in a shopping app. Find the ONE big main product photo of the gallery. "
        "Do NOT include offer banners, the title, price, rating, thumbnails, dots or buttons. "
        "Return JSON: {\"box\": [ymin, xmin, ymax, xmax]} with every number from 0 to 1000 "
        "(1000 = the full height for ymin/ymax and the full width for xmin/xmax)."
    )

    def _parse_box(text):
        """Tolerant: accepts ```json fences, extra words around the JSON, 'box' or 'box_2d', numbers as text."""
        t = (text or "").strip()
        b = None
        mm = re.search(r"\{.*\}", t, re.S)
        if mm:
            try:
                data = json.loads(mm.group(0))
                b = data.get("box") or data.get("box_2d") or data.get("bbox")
            except Exception:
                b = None
        if not b:                                   # last resort: the first 4 numbers in the answer
            nums = re.findall(r"-?\d+(?:\.\d+)?", t)
            b = nums[:4] if len(nums) >= 4 else None
        y1, x1, y2, x2 = [float(v) for v in b]
        return y1, x1, y2, x2

    def validate(text):
        try:
            y1, x1, y2, x2 = _parse_box(text)
            if not (0 <= y1 < y2 <= 1000 and 0 <= x1 < x2 <= 1000):
                return "box numbers must be 0-1000 with min < max"
            if (y2 - y1) * (x2 - x1) < 80000:
                return "box is too small to be the main picture"
        except Exception:
            return "not valid JSON with a 'box' of 4 numbers"
        return None
    try:
        part = types.Part.from_bytes(data=agent.shrink(png, 720), mime_type="image/jpeg")
        y1, x1, y2, x2 = _parse_box(llm.generate([part, prompt], validate=validate))
        return (x1 * W / 1000, y1 * H / 1000, x2 * W / 1000, y2 * H / 1000)
    except Exception as e:
        print("  (AI could not find the product picture area:", str(e)[:100], ")")
        return None


def _sig(png):
    """Tiny grayscale fingerprint of the image-carousel area (to tell if the picture changed)."""
    im = Image.open(io.BytesIO(png)).convert("L")
    w, h = im.size
    return list(crop_product(im).resize((24, 24)).getdata())


def _same(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < 4


def _swipe(d, x1, y1, x2, y2):
    phone.adb("shell", "input swipe %d %d %d %d 350" % (x1, y1, x2, y2))


def _ignore_bad_reference(report):
    """If the reference picture itself shows no product, 'same product' cannot be judged. That is not a fault of the video,
    so it must not cost another 12 minutes of generation."""
    c = report.get("checks", {})
    if c.get("same_product") is False and re.search(r"reference:\s*no product|no product image|no product visible", str(report.get("reason", "")), re.I):
        c["same_product"] = None
        failed = [name for name, v in c.items() if v is False]
        print("  (same_product skipped: the reference picture shows no product, so it cannot be compared)")
        if not failed:
            report["pass"] = True
            report["reason"] = "ok (same product NOT checked: the reference picture showed no product)"
        else:
            report["reason"] = "failed: " + ", ".join(failed)
    return report


_OTHER_PRODUCTS = re.compile(r"similar products|more like this|you may also like|products for you|recommended for you|sponsored", re.I)


def capture_pages(d, out_dir, max_images=6, info_pages=1):
    """1) swipe LEFT on the image carousel and screenshot each product picture,
       2) then scroll DOWN a little to screenshot the product info.
       Returns (carousel_shots, info_shots, texts); shots are (path, png)."""
    W, H = agent._screen(d)
    carousel, info, texts, prev = [], [], [], None
    first = phone.screenshot(d)
    box, how = find_gallery_box(d, W, H), "UI tree"
    if not box:
        box, how = ai_gallery_box(first), "AI"
    GALLERY_BOX["box"] = box
    print("  product picture area on the screen (%s): %s" % (how, tuple(int(v) for v in box) if box else "not found, using the old fixed area"))
    if box:
        sy = int((box[1] + box[3]) / 2)
        sx1, sx2 = int(box[0] + .85 * (box[2] - box[0])), int(box[0] + .15 * (box[2] - box[0]))
    else:
        sy, sx1, sx2 = int(.28 * H), int(.85 * W), int(.15 * W)
    for i in range(max_images):
        png = phone.screenshot(d)
        sig = _sig(png)
        if prev is not None and _same(sig, prev):       # swipe did nothing: last picture reached
            break
        prev = sig
        path = os.path.join(out_dir, "product.png" if i == 0 else "image_%d.png" % (i + 1))
        with open(path, "wb") as f:
            f.write(png)
        carousel.append((path, png))
        texts += screen_texts(d)
        _swipe(d, sx1, sy, sx2, sy)                                          # next picture
        time.sleep(1.5)
    for j in range(info_pages):
        _swipe(d, int(.5 * W), int(.80 * H), int(.5 * W), int(.30 * H))       # scroll down ONCE to the product details
        time.sleep(2)
        now = screen_texts(d)
        if j > 0 and any(_OTHER_PRODUCTS.search(t) for t in now):             # other products started: the details are over
            print("  (other products are showing -> stopped scrolling)")
            break
        png = phone.screenshot(d)
        path = os.path.join(out_dir, "info_%d.png" % (j + 1))
        with open(path, "wb") as f:
            f.write(png)
        info.append((path, png))
        texts += now
    print("  %d product photos + %d info screenshots liye" % (len(carousel), len(info)))
    return carousel, info, texts


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        for name in ("arial.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                pass
    return ImageFont.load_default()


def numbered_collage(shots, cols=3, tw=360):
    """The carousel pictures (product area only) in ONE image, each tile with a red number 1, 2, 3..."""
    tiles = []
    for i, (_, png) in enumerate(shots, 1):
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = crop_product(im)
        im = im.resize((tw, max(1, int(im.height * tw / im.width))))
        dr = ImageDraw.Draw(im)
        dr.rectangle((0, 0, 60, 52), fill=(210, 0, 0))
        dr.text((14, 4), str(i), fill=(255, 255, 255), font=_font(38))
        tiles.append(im)
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    th = max(t.height for t in tiles)
    canvas = Image.new("RGB", (cols * tw, len(rows) * th), (255, 255, 255))
    for r, row in enumerate(rows):
        for c, t in enumerate(row):
            canvas.paste(t, (c * tw, r * th))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    return buf.getvalue()


IMG_TYPES = ("product", "model", "size_chart", "infographic", "other")


def classify_images(carousel):
    """One AI look at all gallery pictures (numbered collage): which is the product itself, which is a person wearing it,
    which is a size chart or a text/infographic picture. Returns {number: {"type":..., "clear":bool, "text":bool}}."""
    shots = carousel[:6]
    prompt = (
        "This is ONE collage of the picture gallery of a single product on a shopping app. Every tile has a red number.\n"
        "Classify EVERY numbered tile:\n"
        "- product: the product itself shown alone (flat lay, on a hanger, on a plain background, or several colours/variants of it side by side)\n"
        "- model: a real person wearing or holding the product\n"
        "- size_chart: a size or measurement table / size guide\n"
        "- infographic: feature icons, headings, offers, or any picture that is mostly added text or graphics\n"
        "- other: anything else (packaging, unrelated photo, group photo)\n"
        "clear = the product is large and clearly visible in the tile. text = big text, table, price or logo ADDED on top of the picture "
        "(print on the product itself does not count).\n"
        "Return JSON: {\"images\": [{\"n\": 1, \"type\": \"product|model|size_chart|infographic|other\", \"clear\": true, \"text\": false}]} "
        "with one entry for every tile."
    )

    def validate(text):
        try:
            items = json.loads(text).get("images")
            if not isinstance(items, list) or len(items) < len(shots):
                return "need one entry for each of the %d tiles" % len(shots)
            for it in items:
                if str(it.get("type", "")).strip().lower() not in IMG_TYPES:
                    return "type must be one of " + ", ".join(IMG_TYPES)
        except Exception:
            return "not valid JSON"
        return None

    try:
        part = types.Part.from_bytes(data=numbered_collage(shots), mime_type="image/jpeg")
        items = json.loads(llm.generate([part, prompt], validate=validate))["images"]
    except Exception as e:
        print("  (gallery photos could not be classified:", str(e)[:100], ")")
        return {}
    out = {}
    for it in items:
        try:
            n = int(it.get("n"))
        except Exception:
            continue
        flag = lambda v, default: default if v is None else (v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes"))
        out[n] = {"type": str(it.get("type", "")).strip().lower(), "clear": flag(it.get("clear"), True), "text": flag(it.get("text"), False)}
    return out


def gallery_for_text(carousel, cls, limit=5):
    """Cropped gallery pictures (JPEG bytes) for reading their printed text. Pictures with added text/infographics/size charts first."""
    order = list(range(min(len(carousel), 6)))
    def has_text(i):
        c = (cls or {}).get(i + 1)
        return bool(c) and (c["type"] in ("infographic", "size_chart", "other") or c["text"])
    order.sort(key=lambda i: (not has_text(i), i))
    out = []
    for i in order[:limit]:
        try:
            im = crop_product(Image.open(carousel[i][0]).convert("RGB"))
            b = io.BytesIO()
            im.save(b, "JPEG", quality=88)
            out.append(b.getvalue())
        except Exception as e:
            print("  (gallery picture %d skipped: %s)" % (i + 1, str(e)[:60]))
    return out


def choose_references(carousel, cls, max_refs=3):
    """Indexes (into carousel) of the pictures to give YouTube Create, best first.
    Only pictures of the PRODUCT itself. Size charts, text/infographic pictures and group photos are never used.
    A photo of a person is used only if there is no product-only picture (then just one, and the prompt asks for a NEW model)."""
    if not cls:
        print("  reference photo: classification not available -> using the first picture only")
        return [0]
    rows = []
    for i in range(min(len(carousel), 6)):
        c = cls.get(i + 1, {"type": "other", "clear": False, "text": False})
        rows.append((i, c))
        print("   picture %d: %s%s%s" % (i + 1, c["type"], "" if c["clear"] else " (not clear)", " (added text)" if c["text"] else ""))
    products = [i for i, c in rows if c["type"] == "product" and c["clear"] and not c["text"]]
    if products:
        picked = products[:max_refs]
    else:
        models = [i for i, c in rows if c["type"] == "model" and c["clear"] and not c["text"]]
        picked = models[:1]
    if not picked:
        picked = [i for i, c in rows if c["type"] in ("product", "model")][:1] or [0]
    print("  reference pictures used for the video: %s (from %d gallery pictures)" % ([i + 1 for i in picked], len(rows)))
    return picked


COMBO_RE = re.compile(r"\bcombo\b|\bfree\b|\+", re.I)
COMBO_RULE = (" This is a COMBO of separate products. Show each item as its own separate object, exactly as in the "
              "reference photo (same shape and colour). Never merge them or hide one inside another.")

_NOISE = re.compile(r"add to cart|buy now|wishlist|rating|review|delivery|return|\u20b9|\brs\.?\s?\d|% off|sold by|seller|cash on|"
                    r"see all|view all|similar products|sponsored|activate windows", re.I)
# size / capacity / quantity / material words: a photo cannot prove or disprove these, so they are never deleted because of the photo
_SPEC = re.compile(r"\d\s*(ml|ltr|litre|liter|l|cm|mm|m|kg|gm|g|w|watt|v|mah|pcs|pc|pack|set|inch|in)\b|"
                   r"\b(plastic|steel|stainless|abs|cotton|polyester|wood|wooden|metal|glass|silicone|leather|nylon|aluminium|aluminum|ceramic|"
                   r"rubber|material|capacity|watt|rpm|warranty)\b", re.I)


_DIM_LABEL = re.compile(r"^product\s+(height|length|width|weight|thickness|depth|diameter|size)$", re.I)
_NUM_ONLY = re.compile(r"^\d+(?:\.\d+)?$")


def _spec_pairs(texts):
    """The Product Highlights card shows label and value as SEPARATE texts ('Product Height', '1.5', 'Product Unit', 'Cm').
    Join them into readable lines such as 'Product Height: 1.5 Cm' so the fact check can find them."""
    clean = [re.sub(r"\s+", " ", t or "").strip() for t in texts]
    unit = next((t for t in clean if t.lower() in _UNIT_WORDS), "")
    out = []
    for k, t in enumerate(clean):
        if _DIM_LABEL.match(t):
            val = next((x for x in clean[k + 1:k + 4] if _NUM_ONLY.match(x)), "")
            if val:
                line = ("%s: %s %s" % (t, val, unit)).strip()
                if line not in out:
                    out.append(line)
    return out


def build_page_text(texts, limit=1500):
    """The text Appium read from the app screens (title, product highlights, details), without price/rating/button noise.
    This is a TRUSTED source for material, size, capacity, quantity and specs (a photo cannot show those)."""
    seen, out = set(), []
    for t in texts:
        t = re.sub(r"\s+", " ", t or "").strip()
        if not re.search(r"[A-Za-z0-9\u0900-\u097F]", t) or t.lower() in seen or _NOISE.search(t):
            continue
        seen.add(t.lower())
        out.append(t)
    return " | ".join(_spec_pairs(texts) + out)[:limit]


def _page_supports(claim, page_text):
    """True if the page says it: the whole phrase, or most of its words (prefix match: 'supported' ~ 'support').
    page_text here = everything trusted: the page text, the full title from the link, and the text printed on the pictures."""
    c, pt = claim.lower().strip(), (page_text or "").lower()
    if c and c in pt:
        return True
    toks = [t for t in re.findall(r"[a-z0-9]+", c) if len(t) > 1]
    if not toks:
        return False
    words = set(re.findall(r"[a-z0-9]+", pt))
    def has(t):
        return t in words or (len(t) > 3 and any(w.startswith(t[:4]) and (w.startswith(t) or t.startswith(w)) for w in words if len(w) > 3))
    sig = [t for t in toks if len(t) > 3] or toks
    return sum(has(t) for t in sig) >= max(1, int(round(0.6 * len(sig))))


def trusted_text(p):
    """Every source that can prove a spec/feature: page text, full title from the link, text printed on the pictures."""
    return " ".join([p.get("page_text") or "", factcheck.url_title(p.get("url") or ""), p.get("name") or "", p.get("image_text") or ""])


def ground_product(p, ref_path):
    """The name/description came from page text and small screenshots, and the AI can invent details there
    (for example 'butterfly print' on a T-shirt set that really shows cats, a heart, 'Princess' and pandas).
    One careful look at the real reference picture: describe what is VISIBLE (this becomes the main source of truth
    for the script and caption) and delete description sentences the picture contradicts."""
    try:
        with open(ref_path, "rb") as f:
            png = f.read()
        prompt = (
            "Look closely at this product picture.\nProduct name: " + p["name"] + "\n"
            "Description taken from the page (may contain mistakes): " + (p["description"] or "none") + "\n"
            "Text read from the app screens (TRUSTED for size, capacity, material, quantity and specs): " + (p.get("page_text") or "none")[:1200] + "\n"
            "Text printed on the product's own pictures (TRUSTED for specs, charging type, what is in the box): " + (p.get("image_text") or "none")[:1200] + "\n\n"
            "1) summary: describe ONLY the product itself (not people) in 1 to 3 plain sentences: how many items, the colour of each, "
            "and EXACTLY what each print, graphic or text on it shows (read any words). Do not guess what you cannot see.\n"
            "2) wrong_claims: short phrases from the page description that describe the LOOK (a print, pattern, colour, shape, number of items) "
            "and that the picture clearly contradicts. NEVER list size, capacity (ml, cm), material, quantity, specs or anything the screen text "
            "above confirms: a photo cannot judge those. Empty list if none.\n"
            "Return JSON: {\"summary\": \"...\", \"wrong_claims\": [\"...\"]}"
        )

        def validate(text):
            try:
                data = json.loads(text)
                if not str(data.get("summary", "")).strip():
                    return "no summary"
                if not isinstance(data.get("wrong_claims", []), list):
                    return "wrong_claims must be a list"
            except Exception:
                return "not valid JSON"
            return None

        part = types.Part.from_bytes(data=agent.shrink(png, 900), mime_type="image/jpeg")
        out = json.loads(llm.generate([part, prompt], validate=validate))
    except Exception as e:
        print("  (product photo could not be double-checked:", str(e)[:100], ")")
        return p
    q = dict(p)
    q["visible"] = clean_desc(out.get("summary", ""))
    desc = p.get("description") or ""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", desc) if s.strip()]
    for w in out.get("wrong_claims", []):
        w = str(w).strip().lower()
        if len(w) < 4:
            continue
        if _SPEC.search(w) or _page_supports(w, trusted_text(p)):
            print("  kept in the description (size/material/spec or the page text says so): '" + w + "'")
            continue
        kept = [s for s in sents if w not in s.lower()]
        if len(kept) < len(sents):
            print("  removed from the description (the picture does not show it): '" + w + "'")
            sents = kept
    q["description"] = " ".join(sents)
    print("  Photo me asli me dikh raha hai:", q["visible"])
    return q


def get_product(shots, texts):
    """AI reads the product name + description from all screenshots and the on-screen text. No questions asked."""
    name, desc = "", ""
    seen, hint = set(), []
    for t in texts:
        t = clean_desc(t)
        if t and t not in seen:
            seen.add(t)
            hint.append(t)
    hint_text = " | ".join(hint)[:2500]
    prompt = (
        "These are screenshots of one product page in the Meesho shopping app (product photos and the info section below them).\n"
        "Text that was found on the screens (may include buttons and noise): " + hint_text + "\n\n"
        "Find the product. Return JSON: {\"name\": \"short product name, 3-8 words, no price\", "
        "\"description\": \"1-2 short sentences with the real features (material, size, use, what is in the box) "
        "that are visible. No price, discount, rating or delivery info. Do not invent anything.\"}"
    )

    def validate(text):
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or not str(data.get("name", "")).strip():
                return "no product name in answer"
        except Exception:
            return "not valid JSON"
        return None

    try:
        imgs = [types.Part.from_bytes(data=agent.shrink(png, 720), mime_type="image/jpeg") for _, png in shots[:4]]
        info = json.loads(llm.generate(imgs + [prompt], validate=validate))
        name = str(info.get("name", "")).strip()
        desc = clean_desc(info.get("description", ""))
    except Exception as e:
        print("  AI product page nahi padh paya:", str(e)[:120])
    print("\nProduct page se mila:")
    print("  Naam       :", name or "(nahi mila)")
    print("  Description:", desc or "(nahi mila)")
    if not name:        # only ask when the AI could not find it at all
        name = input("Naam nahi mila. Product ka naam type karo: ").strip()
    if not name:
        raise RuntimeError("Product ka naam nahi mila. Dobara chalao.")
    return {"name": name, "description": desc}


def list_mp4():
    pattern = " ".join(f + "/*.mp4" for f in VID_FOLDERS)
    out = phone.adb("shell", "ls -t " + pattern + " 2>/dev/null")
    return [l.strip() for l in out.splitlines() if l.strip().startswith("/")]


def create_video(d, p, shot_remote, prompt=None, n_images=1):
    """YouTube Create via yt_flow (fixed, verified UI steps, no AI). Returns the mp4 path on the phone.
    prompt = the researched script prompt from script_writer (falls back to the old plain prompt)."""
    if not prompt:
        prompt = STYLE + " Product: " + p["name"] + ". Details: " + p["description"]
    return yt_flow.run(d, prompt, n_images)


LAST_POST = {"caption": "", "tags": "", "search": []}


def _caption_ready():
    c = (LAST_POST.get("caption") or "").strip()
    return bool(c) and not c.startswith("(Caption nahi bana")


def write_post(out_dir, url, p, script=None, report=None, regen=False):
    """Writes post.txt. The caption is written by the AI only ONCE per run: the first call makes it (right after the prompt),
    later calls (after the video) reuse it, so the caption you already saw does not change. regen=True forces a new one."""
    st = (script or {}).get("style") or {}
    cta_line = st.get("caption_cta") or CTA_LINE          # rotating wording, always contains "Comment LINK" (the auto-DM keyword)
    tone = st.get("caption_tone") or ""                   # pure content / light / direct: how much the caption sounds like an ad
    if (script or {}).get("caption_hook"):
        tone += "A possible opening idea (rewrite freely, keep it natural): " + script["caption_hook"] + " "
    if script is not None and not script.get("spoken_cta", True):
        tone += "The video does NOT say the call to action out loud: the program adds the 'Comment LINK' line at the end of the caption, so let your lines lead into it. "
    if tone:
        tone += "\n"
    prompt = (
        "You write Instagram Reel captions for an Indian affiliate creator.\n"
        "Product: " + p["name"] + "\n"
        "Details from the page: " + (p["description"] or "none") + "\n"
        + (("What the product photo really shows (looks): " + p["visible"] + "\n") if p.get("visible") else "")
        + (("Specs read from the page: " + p["page_text"][:600] + "\n") if p.get("page_text") else "") +
        "Use ONLY these facts. Do not invent discounts, ratings, materials, sizes or claims.\n"
        "Rules: simple Hinglish (Hindi in English letters), cute friendly tone, no price, no words like 'guaranteed' or 'best', "
        "at most 3 emojis.\n"
        + tone +
        "caption = ONLY the opening: line 1 is a catchy hook, then 1 or 2 short lines (what the product is, or one relatable moment). "
        "Do NOT write the affiliate disclosure, the call to action, hashtags or search phrases: the program adds them.\n"
        "hashtags = 5 to 8 hashtags about this product and Meesho. Do not use #ad.\n"
        "search_phrases = 8 to 10 short things people type into Instagram search about this product: 2 to 6 words each, plain English, "
        "a mix of (a) product searches, for example 'cute cat toys', (b) discovery phrases, for example 'Meesho finds' or 'cute finds', and "
        "(c) 2 or 3 questions, for example 'where to find cute cat toys?'. No hashtags, no emoji, no price, no 'best', 'cheap' or 'offer'.\n"
        "Return JSON: {\"caption\": \"...\", \"hashtags\": [\"#...\"], \"search_phrases\": [\"...\"]}"
    )
    if _caption_ready() and not regen:
        caption, tags, cta_line = LAST_POST["caption"], LAST_POST["tags"], LAST_POST.get("cta") or cta_line
    else:
        caption, tags, search = "", "", []
        try:
            data = json.loads(llm.generate([prompt]))
            caption = str(data.get("caption", "")).strip()
            h = data.get("hashtags", [])
            tags = " ".join(h) if isinstance(h, list) else str(h)
            search = data.get("search_phrases", [])
        except Exception as e:
            print("  caption nahi bana:", str(e)[:150])
            caption = "(Caption nahi bana. Thodi der baad dobara try karo.)"
        LAST_POST["caption"], LAST_POST["tags"], LAST_POST["cta"] = caption, tags, cta_line
        LAST_POST["search"] = _search_phrases(search, p)
    text = ("PRODUCT: " + p["name"] + "\nLINK: " + url + "\n\nCAPTION (exactly as it is posted):\n" + (build_instagram_caption() or caption) + "\n")
    if script:
        _st = script.get("style") or {}
        text += "\nREEL FORMAT: %s | voice: %s | job: %s | ad level: %s\n" % (_st.get("label"), _st.get("voice"), _st.get("job"), _st.get("ad"))
        text += "\nVOICEOVER SCRIPT:\n" + script["voiceover"] + "\n"
    if report:
        text += ("\nVIDEO CHECK: " + ("PASS" if report.get("pass") else "FAILED - dekh ke hi post karna") + "\n"
                 + "Reason: " + str(report.get("reason")) + "\n"
                 + "What the video says: " + str(report.get("transcript")) + "\n"
                 + "What the video shows: " + str(report.get("summary")) + "\n")
    path = os.path.join(out_dir, "post.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    print("\n" + text)
    return path


CTA_LINE = "Comment LINK to get the link in your DM"
DISCLOSURE = "(Affiliate link - I may earn a commission)"
SHOW_AD_TAG = os.getenv("CAPTION_AD_TAG", "0").strip().lower() in ("1", "true", "yes", "on")             # off by default; CAPTION_AD_TAG=1 in .env adds #ad again
SHOW_DISCLOSURE = os.getenv("CAPTION_DISCLOSURE", "0").strip().lower() in ("1", "true", "yes", "on")    # off by default; set CAPTION_DISCLOSURE=1 in .env to add the line again


_SEARCH_BAD = re.compile(r"\b(best|cheap|cheapest|price|offer|offers|discount|sale|guaranteed|free|rs|inr)\b|[%\u20b9#]", re.I)
_SEARCH_FILL = ["Meesho finds", "Meesho haul", "Meesho products", "cute finds", "Instagram finds", "online shopping finds",
                "where to find cute stuff?"]


def _search_phrases(raw, p=None):
    """7 to 10 plain search phrases for the end of the caption (so the Reel can be found in Instagram search)."""
    if isinstance(raw, str):
        raw = re.split(r"[\n|;]+", raw)
    out, seen = [], set()

    def add(t):
        t = re.sub(r"\s+", " ", str(t or "")).strip(" -*\u2022,.")
        t = t.replace('"', "")
        if not (2 <= len(t) <= 60) or _SEARCH_BAD.search(t) or any(ord(c) > 0x2000 for c in t):
            return
        if t.lower() in seen:
            return
        seen.add(t.lower())
        out.append(t[:1].upper() + t[1:])
    for t in (raw if isinstance(raw, list) else []):
        add(t)
    for t in _SEARCH_FILL:                                   # fewer than 7 usable phrases: fill up with safe general ones
        if len(out) >= 7:
            break
        add(t)
    return out[:10]


def _hashtags(tags):
    found = ["#" + t.lstrip("#") for t in re.findall(r"#?\w+", tags or "")]        # accepts "#a #b", "a, b" and lists joined by spaces
    out, seen = [], set()
    for t in found:
        if len(t) > 1 and t.lower() not in seen and t.lower() != "#ad":
            seen.add(t.lower())
            out.append(t)
    return out[:8] + (["#ad"] if SHOW_AD_TAG else [])


def build_instagram_caption():
    """The caption exactly as it is typed in Instagram, always in this order:
         hook line + 1-2 short lines (written by the AI)
         (Affiliate link - I may earn a commission)   <- only when CAPTION_DISCLOSURE=1 in .env
         Comment LINK ... (rotating wording; the auto-DM listens for "Comment LINK")
         <blank line>
         #hashtags (and #ad only when CAPTION_AD_TAG=1 in .env)
         <blank line>
         search phrases, separated by commas
       Returns None if the caption is not usable."""
    cap = (LAST_POST["caption"] or "").strip()
    if not cap or cap.startswith("(Caption nahi bana"):
        return None
    lines = [l.strip() for l in cap.splitlines() if l.strip()]
    body = [l for l in lines if DISCLOSURE.lower() not in l.lower()
            and not (re.search(r"comment\s+link", l, re.I) and len(l) <= 90)]       # the program adds these two lines itself
    body = body or [l for l in lines if DISCLOSURE.lower() not in l.lower()] or lines
    cta = LAST_POST.get("cta") or CTA_LINE
    out = "\n".join(body + ([DISCLOSURE] if SHOW_DISCLOSURE else []) + [cta]) + "\n\n" + " ".join(_hashtags(LAST_POST["tags"]))
    search = LAST_POST.get("search") or _search_phrases([], {})
    if search:
        out += "\n\n" + ", ".join(search)
    return out.strip()[:2200]


def publish_caption(out_dir):
    """Writes caption.txt (exactly what will be typed in Instagram) and prints one CAPTION>> line for the control page."""
    cap = build_instagram_caption()
    if not cap:
        print("  (caption nahi bana: Instagram caption box khali rahega)")
        return None
    with open(os.path.join(out_dir, "caption.txt"), "w", encoding="utf-8-sig") as f:
        f.write(cap)
    print("CAPTION>> " + json.dumps(cap, ensure_ascii=False))       # the control page shows this in its own box with a Copy button
    return cap


SCAN_ACTION = "android.intent.action.MEDIA_SCANNER_SCAN_FILE"


def scan_file(path):
    """Tell Android's gallery database about a new / changed / deleted file, so the picker shows the right order."""
    try:
        phone.adb("shell", "am", "broadcast", "-a", SCAN_ACTION, "-d", "file://" + path)
    except Exception as e:
        print("  (media scan skipped:", str(e)[:80], ")")


def clean_reference_shots():
    """Delete the temporary product pictures (product_shot_*) that this program pushed for YouTube Create.
    They were touched to be the NEWEST item in the picker, so left on the phone they come BEFORE the Reel video in the
    Instagram gallery and the bot picked the picture instead of the video."""
    try:
        names = (phone.adb("shell", "ls /sdcard/Pictures 2>/dev/null") or "").split()
    except Exception as e:
        print("  (could not list the phone pictures:", str(e)[:80], ")")
        return
    n = 0
    for name in names:
        if name.startswith("product_shot_"):
            path = "/sdcard/Pictures/" + name
            try:
                phone.adb("shell", "rm", "-f", path)
                scan_file(path)             # a scan of a deleted path removes it from the gallery database
                n += 1
            except Exception as e:
                print("  (could not delete %s: %s)" % (name, str(e)[:60]))
    if n:
        print("  %d temporary product picture(s) removed from the phone gallery (so the video is the newest item)" % n)


def make_video_newest(remote_video):
    """The Reel video must be the very first item of the Instagram gallery: new date + gallery scan + no newer pictures."""
    try:
        phone.adb("shell", "touch", remote_video)
    except Exception as e:
        print("  (touch failed:", str(e)[:80], ")")
    scan_file(remote_video)
    clean_reference_shots()
    time.sleep(3)


def post_to_instagram(d, out_dir, p, report, script=None, with_dm=True):
    """Post video.mp4 as a Reel, then link the Meesho Creator Club auto-DM (keyword: Link).
    Refuses to post when the checks did not pass or the voice could not be checked."""
    checks = (report or {}).get("checks", {})
    if not report or not report.get("pass"):
        print("\nPOST NAHI KIYA: video checks pass nahi hue (%s)." % (report or {}).get("reason"))
        return False
    vc = (script or {}).get("voice_check", "strict")
    if vc == "presence":
        print("\nPOST NAHI KIYA: gaane / jingle ke shabd check nahi ho sakte. Khud sun ke post karo.")
        return False
    if (vc == "strict" and checks.get("pronunciation") is None) or checks.get("same_product") is None:
        print("\nPOST NAHI KIYA: voice ya product match check nahi ho paya. Khud dekh ke post karo.")
        return False
    cap = build_instagram_caption()
    if not cap:
        print("\nPOST NAHI KIYA: caption nahi bana.")
        return False
    marker = os.path.join(out_dir, "posted.txt")
    if os.path.exists(marker):
        print("\nPOST NAHI KIYA: is folder ka video pehle hi post ho chuka hai (posted.txt).")
        return False
    video = os.path.join(out_dir, "video.mp4")
    remote_video = "/sdcard/Movies/reel_%s.mp4" % os.path.basename(out_dir)
    print("\n6/7 Instagram Reel post ho raha hai...")
    print("  caption:\n" + cap)
    odd = sorted(set(c for c in cap if ord(c) > 127))
    if odd:
        print("  NOTE: the typer can only enter plain keyboard characters, these will be left out of the caption:", " ".join(odd[:14]))
    phone.adb("shell", "rm", "-f", remote_video)
    phone.push_media(video, remote_video)
    make_video_newest(remote_video)         # newest date + gallery scan + product pictures deleted: the video is the FIRST item
    phone.open_app(d, PKG["instagram"])
    ok, why = post_guard.post_reel(d, cap, os.path.basename(remote_video))     # checks every stage, never shares unless all is right
    print("  instagram:", "OK" if ok else "FAIL", "-", why)
    if not ok:
        print("POST SHAYAD NAHI HUA. Instagram khol ke check karo, phir manually post karo. Caption post.txt me hai.")
        return False
    with open(marker, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    if not with_dm:
        print("7/7 Auto-DM: is run me nahi manga gaya, skip.")
        return ok
    time.sleep(30)
    print("7/7 Meesho Creator Club auto-DM link ho raha hai...")
    ok2, why2 = auto_dm.link_auto_dm(d, p.get("url") or "", p.get("name") or "")      # kills Meesho, wishlists fresh, links the auto-DM
    print("  creator club:", "OK" if ok2 else "FAIL", "-", why2)
    if not ok2:
        print("Reel post ho gaya, par Creator Club auto-DM link NAHI hua. Meesho me manually link karo.")
    else:
        with open(os.path.join(out_dir, "autodm.txt"), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + str(why2) + "\n")
    return ok and ok2


def post_gallery_reel(d, out_dir):
    """Only post: the NEWEST video in the phone's gallery becomes a Reel, with the caption typed on the control page
    (the page saves it to the file named in CAPTION_FILE). Nothing is made, checked or auto-DM'd."""
    path = os.getenv("CAPTION_FILE", "").strip()
    cap = ""
    try:
        with open(path, encoding="utf-8-sig") as f:
            cap = f.read().replace("\r\n", "\n").strip()[:2200]
    except Exception as e:
        print("  (caption file could not be read:", str(e)[:100], ")")
    if not cap:
        print("\nPOST NAHI KIYA: caption khali hai. Page par caption likho aur dobara Start karo.")
        return False
    print("2/3 Phone ki gallery me sabse naya video dhoondh raha hoon...")
    files = list_mp4()
    if not files:
        print("\nPOST NAHI KIYA: phone me koi .mp4 video nahi mila (folders: %s)." % ", ".join(VID_FOLDERS))
        return False
    newest = files[0]
    when = ""
    try:
        when = (phone.adb("shell", "stat -c '%y' '" + newest + "'") or "").strip()[:19]
    except Exception:
        pass
    print("  sabse naya video:", newest, ("(" + when + ")") if when else "")
    odd = sorted(set(c for c in cap if ord(c) > 127))
    if odd:
        print("  WARNING: ye characters phone par shayad type nahi honge ya galat honge:", " ".join(odd[:14]))
    with open(os.path.join(out_dir, "caption.txt"), "w", encoding="utf-8-sig") as f:
        f.write(cap)
    print("  caption (%d characters):\n%s" % (len(cap), cap))
    print("3/3 Instagram Reel post ho raha hai...")
    print("PHONE>> busy|The bot is posting the Reel on Instagram.")
    try:
        # make a fresh copy of the video so it is the NEWEST file in the gallery (a newer screenshot or picture must not come first)
        fresh = "/sdcard/Movies/reel_%s.mp4" % time.strftime("%Y%m%d_%H%M%S")
        try:
            phone.adb("shell", "cp", newest, fresh)
            phone.adb("shell", "touch", fresh)
            scan_file(fresh)
            clean_reference_shots()         # old product_shot_* pictures must not come before the video
            time.sleep(3)
        except Exception as e:
            print("  (could not make the video the newest file:", str(e)[:80], ")")
        phone.open_app(d, PKG["instagram"])
        ok, why = post_guard.post_reel(d, cap, os.path.basename(newest))       # checks every stage, never shares unless all is right
    finally:
        print("PHONE>> free|Finished. The phone is yours.")
    print("  instagram:", "OK" if ok else "FAIL", "-", why)
    if not ok:
        print("POST SHAYAD NAHI HUA. Instagram khol ke check karo. Caption caption.txt me hai:", out_dir)
        return False
    with open(os.path.join(out_dir, "posted.txt"), "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + newest + "\n")
    print("\nTAIYAAR! Reel post ho gayi. Folder:", out_dir)
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    # MODE from the control page. Every level includes the ones before it:
    #   prompt = YT Create prompt + Instagram caption | video = + the video | post = + the Instagram Reel post and auto-DM
    mode_env = {"prompt_only": "prompt", "yt_only": "video", "yt_instagram": "autodm"}.get(
        os.getenv("MODE", "").strip().lower(), os.getenv("MODE", "").strip().lower())
    # post = Instagram Reel only | autodm = Reel + Meesho Creator Club auto-DM | reelpost = ONLY post the newest gallery video
    do_post = "--post" in sys.argv[1:] or mode_env in ("post", "autodm")
    with_dm = "--post" in sys.argv[1:] or mode_env == "autodm"
    reel_only = mode_env == "reelpost"
    dm_only = mode_env == "autodmonly"       # TEST: no video, no post. Only wishlist the product + link the Meesho auto-DM
    prompt_only = "--prompt-only" in sys.argv[1:] or mode_env == "prompt"           # prompt + caption only, no video
    if len(args) < 1 and not reel_only:
        print('Use: python main_link.py "PRODUCT_LINK" [--post]')
        return
    url = args[0].strip() if args else ""
    os.makedirs(LOGS, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, stamp)
    os.makedirs(out_dir, exist_ok=True)
    d = None
    try:
        if reel_only:
            print("Settings: mode=only post the newest gallery video as a Reel")
            print("1/3 Phone connect ho raha hai...")
            d = phone.connect()
            if not post_gallery_reel(d, out_dir):
                sys.exit(1)
            return
        if dm_only:
            print("Settings: mode=only the Meesho auto-DM (test): no video is made, nothing is posted")
            print("1/3 Phone connect ho raha hai...")
            d = phone.connect()
            print("2/3 Meesho Creator Club auto-DM link ho raha hai...")
            print("PHONE>> busy|The bot is linking the Meesho auto-DM.")
            try:
                ok_dm, why_dm = auto_dm.link_auto_dm(d, url, "")
            finally:
                print("PHONE>> free|Finished. The phone is yours.")
            print("  creator club:", "OK" if ok_dm else "FAIL", "-", why_dm)
            if not ok_dm:
                print("Auto-DM link NAHI hua. Logs / logs/autodm_*.png dekho.")
                sys.exit(1)
            print("3/3 Auto-DM test khatam.")
            return
        print("Settings: mode=%s | video type=%s | spoken CTA=%s | text on video=%s"
              % ("prompt + caption only" if prompt_only else ("prompt + caption + video + Instagram post" if do_post else "prompt + caption + video"),
                 os.getenv("VIDEO_TYPE", "auto"), os.getenv("CTA", "random"), os.getenv("TEXT_OVERLAY", "false")))
        web = None
        if SCRAPE in ("web", "auto"):
            print("1/5 Product page PC ke browser me khul raha hai (phone use nahi hoga)...")
            try:
                import meesho_web
                web = meesho_web.fetch(url, out_dir)
            except Exception as e:
                print("  PC browser se product page nahi padh paya:", str(e)[:300])
                if SCRAPE == "web":
                    raise RuntimeError("Web se product nahi mila (SCRAPE=web, phone wala tareeka band hai): " + str(e)[:200])
                print("  -> phone wala tareeka try kar raha hoon")
        if web:
            carousel, info, texts = web
            GALLERY_BOX["full"] = True
        else:
            print("1/5 Phone connect ho raha hai...")
            d = phone.connect()
            print("2/5 Link phone pe khol raha hoon...")
            if not open_link(d, url):
                raise RuntimeError("Link phone pe nahi khula. Link sahi hai? Meesho app installed hai?")
            time.sleep(10)
            print("3/5 Product ki photos (carousel) aur info ke screenshots le raha hoon...")
            carousel, info, texts = capture_pages(d, out_dir)
        if not carousel:
            raise RuntimeError("Product page ka screenshot nahi mila.")
        print("   Gallery ki photos pehchan raha hoon (product / model / size chart)...")
        cls = classify_images(carousel)
        if cls and not GALLERY_BOX.get("full") and not any(c["type"] in ("product", "model") for c in cls.values()):
            print("  (no product picture in the crops -> asking the AI where the picture is on the screen)")
            _b = ai_gallery_box(carousel[0][1])
            if _b:
                GALLERY_BOX["box"] = _b
                cls = classify_images(carousel)
        refs = choose_references(carousel, cls)
        ref_files = []         # cropped to the product picture only (no app screen, prices or buttons)
        for _n, _idx in enumerate(refs):
            try:
                _im = Image.open(carousel[_idx][0])
                _ref = os.path.join(out_dir, "reference.png" if _n == 0 else "reference_%d.png" % (_n + 1))
                crop_product(_im).save(_ref)
                ref_files.append(_ref)
            except Exception as e:
                print("  (reference crop skipped:", str(e)[:80], ")")
        shot_local = ref_files[0] if ref_files else carousel[0][0]
        print("   Product ki jaankari nikal raha hoon...")
        ai_shots = carousel[:1] + info + carousel[1:2]       # main photo, info section, one more photo
        p = get_product(ai_shots, texts)
        p["page_text"] = build_page_text(texts)
        p["url"] = url                                    # the link holds the full product title
        p["gallery"] = gallery_for_text(carousel, cls)    # the product's own pictures, text/spec pictures first
        image_facts.read(p)                               # specs and features PRINTED on those pictures (trusted source)
        p = ground_product(p, shot_local)       # the photo decides LOOKS (prints, colours); the page text decides specs
        print("   Product ki research + viral Hinglish script ban rahi hai...")
        script = script_writer.make_script(p)
        print("  Voiceover:", script["voiceover"])
        print("  Voice text (Devanagari):", script.get("voice_hi", ""))
        print("  Meaning:", script.get("meaning", ""))
        _st = script.get("style") or {}
        print("  Reel format: %s | voice: %s | job: %s | ad level: %s | spoken CTA: %s"
              % (_st.get("label"), _st.get("voice"), _st.get("job"), _st.get("ad"), script.get("spoken_cta")))
        prompt = script["yt_prompt"]
        is_combo = bool(COMBO_RE.search((p["name"] + " " + url.replace("-", " ")).lower()))
        if is_combo:
            prompt = prompt.rstrip() + COMBO_RULE
            print("  combo product detected -> added the 'keep items separate' rule to the prompt")
        print("  YouTube prompt (%d chars): %s" % (len(prompt), prompt))
        with open(os.path.join(out_dir, "yt_prompt.txt"), "w", encoding="utf-8-sig") as _f:
            _f.write(prompt)
        print("YTPROMPT>> " + prompt)         # the control page shows this line in its own box with a Copy button
        print("   Instagram caption ban raha hai (video se pehle, taaki atak jaye to bhi mil jaye)...")
        write_post(out_dir, url, p, script, None)       # post.txt + the caption (written once, reused after the video)
        publish_caption(out_dir)
        if prompt_only:
            print("\nPROMPT + CAPTION READY. Video NAHI banaya (prompt + caption mode).")
            print("Reference photos for YouTube Create:", ", ".join(ref_files or [shot_local]))
            print("Folder:", out_dir)
            try:
                os.startfile(out_dir)
            except Exception:
                pass
            return
        if d is None:       # the product page was read on the PC: the phone is needed only from here (video + posting)
            print("   Ab video ke liye phone connect ho raha hai...")
            d = phone.connect()
        best, report = None, None
        MAX_TRIES = int(os.getenv("MAX_VIDEO_TRIES", "5"))
        for attempt in range(1, MAX_TRIES + 1):
            supervisor.reset()
            shot_remote = "/sdcard/Pictures/product_shot_%d.png" % int(time.time())   # new name = newest in the picker
            for _n, _p2 in reversed(list(enumerate(ref_files[1:], 2))):       # extra product pictures first, main one last (= newest)
                try:
                    _r2 = "/sdcard/Pictures/product_shot_%d_%d.png" % (int(time.time()), _n)
                    phone.push_media(_p2, _r2)
                    phone.adb("shell", "touch", _r2)
                    time.sleep(1.5)
                except Exception as _e:
                    print("  (extra photo skipped:", str(_e)[:80], ")")
            phone.push_media(shot_local, shot_remote)
            phone.adb("shell", "touch", shot_remote)
            time.sleep(2)
            print("4/5 YouTube Create me video ban raha hai, try %d/%d (kuch minute lag sakte hain)..." % (attempt, MAX_TRIES))
            try:
                remote = create_video(d, p, shot_remote, prompt, n_images=max(1, len(ref_files)))
            except supervisor.FlowProblem as fp:        # the watcher saw an error on screen while waiting
                print("  [watcher] stopped waiting:", fp)
                prompt = supervisor.fix_prompt(p, script, prompt, fp, attempt)
                continue
            except RuntimeError as e:                   # a fixed step failed: ask the AI what the screen shows
                print("  [flow] step failed:", str(e)[:200])
                if supervisor.unstick(d, e):            # unexpected dialog/screen: AI clears it, same prompt again
                    continue
                fp = supervisor.diagnose_failure(d, e)
                prompt = supervisor.fix_prompt(p, script, prompt, fp, attempt)
                continue
            except Exception as e:                      # e.g. Appium session died: reconnect and try again
                print("  [flow] unexpected error:", str(e)[:200], "-> reconnecting to the phone")
                try:
                    d.quit()
                except Exception:
                    pass
                time.sleep(3)
                d = phone.connect()
                continue
            local = os.path.join(out_dir, "video_try%d.mp4" % attempt)
            phone.adb("pull", remote, local)
            if not os.path.exists(local) or os.path.getsize(local) == 0:
                raise RuntimeError("Video laptop pe copy nahi hua: " + remote)
            print("  video saved:", local)
            try:
                import trim
                local = trim.trim_reference_end(local, shot_local)   # cut the screenshot added at the end
            except Exception as e:
                print("  (trim skipped:", str(e)[:80], ")")
            print("   Video ke frames aur awaaz check ho rahi hai...")
            try:
                report = verify_video.check_video(local, p, script)
            except Exception as e:
                report = {"pass": False, "reason": "check could not run: " + str(e)[:150],
                          "transcript": "", "summary": "", "checks": {}}
            # The free AI providers cannot hear audio. Do not throw away a good video (and 12 minutes of
            # generation) only because the audio could not be checked.
            c, rs = report.get("checks", {}), str(report.get("reason", "")).lower()
            if (not report["pass"] and "audio" in rs and "not provided" in rs
                    and c.get("frames_show_product") and c.get("frames_clean") and c.get("no_bad_claims")):
                report["pass"] = True
                report["reason"] = "frames OK; audio could NOT be checked by AI - listen to it yourself before posting"
            # Strict, code-based checks: the words the voice really said vs the script, and the product vs the reference photo
            try:
                report = strict_checks.apply(report, local, shot_local, p, script, ref_paths=ref_files or [shot_local])
            except Exception as e:
                print("  (strict checks could not run:", str(e)[:120], ")")
            report = _ignore_bad_reference(report)
            print("  check:", report["checks"], "->", report["reason"])
            print("  said :", report["transcript"])
            best = local
            if report["pass"]:
                break
            prompt = script_writer.retry_prompt(p, script, report["reason"])
            if is_combo:
                prompt = prompt.rstrip() + COMBO_RULE
        if not best:
            raise RuntimeError("Video %d tries me bhi nahi bana. Logs dekho." % MAX_TRIES)
        import shutil
        final = os.path.join(out_dir, "video.mp4")
        shutil.copyfile(best, final)
        if not report["pass"]:
            print("\nWARNING: video 2 baar check me fail hui. Video rakh li hai, par post karne se pehle khud dekh lo.")
        print("5/5 post.txt me video check ki report jod raha hoon (caption wahi hai jo pehle bana tha)...")
        write_post(out_dir, url, p, script, report)
        print("\nTAIYAAR! Folder:", out_dir)
        print("Video phone ki gallery mein bhi hai. Dekh lo, sahi lage to post kar do.")
        try:
            os.startfile(out_dir)
        except Exception:
            pass
        if do_post:
            try:
                post_to_instagram(d, out_dir, p, report, script, with_dm=with_dm)
            except Exception as e:      # the video + caption are already saved, so this is not a total failure
                print("\nPOSTING FAIL:", str(e)[:200], "- video aur caption folder me safe hain, manually post karo.")
    except Exception as e:
        print("\nFAILED:", e)
        try:
            with open(os.path.join(LOGS, "fail_link_" + stamp + ".png"), "wb") as f:
                f.write(phone.screenshot(d))
        except Exception:
            pass
        sys.exit(1)
    finally:
        try:
            if d:
                d.quit()
        except Exception:
            pass


import user_feedback, phone_focus
user_feedback.install(globals())     # FIRST: your notes from the page (change the video / help on the phone) + "Make again with this change"
phone_focus.install(globals())       # then: gives the phone back to you while the bot does not need it


main()
