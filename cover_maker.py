"""cover_maker.py - builds the custom Instagram Reel cover on the API side (the phone is not touched here).

Design (Option B): the image model only paints a TEXT-FREE pastel background. Everything that matters is drawn by Pillow:
the REAL product photo (never redrawn by an AI), the headline, the sticker with 4 points, the callout, the bottom strip.

    job = cover_maker.start(out_dir, p, script)        # runs in a background thread, can start early
    res = cover_maker.wait(job)                        # {"ok": bool, "path": cover.png or None, "reason": str, "attempts": n}

    res = cover_maker.build_cover(out_dir, p, script)  # same thing, blocking (used by test_cover.py)

Files written to the run folder: cover.png (only when verified), cover_prompt.txt (background prompt + text fields + result),
cover_failed_last.png (last try when all attempts failed, so you can look at it).

.env (all optional): COVER_STEP=1, COVER_ATTEMPTS=3, COVER_FACTCHECK=1, COVER_ALLOW_UNVERIFIED=0, COVER_FONT_DIR=assets/fonts,
COVER_FONT_BOLD=Baloo2-Bold.ttf, COVER_FONT_REG=Baloo2-Medium.ttf, COVER_NO_IMAGE_API=0 (1 = gradient background only).
"""
import glob, io, json, math, os, random, re, sys, threading, time
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat
from google.genai import types
import llm

W, H = 1080, 1920
BASE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.getenv("COVER_FONT_DIR", os.path.join(BASE, "assets", "fonts"))
FONT_BOLD = os.getenv("COVER_FONT_BOLD", "Baloo2-Bold.ttf")
FONT_REG = os.getenv("COVER_FONT_REG", "Baloo2-Medium.ttf")
ATTEMPTS = int(os.getenv("COVER_ATTEMPTS", "3"))
ICONS = ["gift", "heart", "star", "diamond", "check", "sparkle"]
INK = (74, 48, 46)
CREAM = (255, 246, 228)

# one palette per attempt: a retry changes the background and colours, never the product
PALETTES = [
    {"name": "soft rose pink and peach", "bg": ((250, 222, 212), (238, 186, 178)), "accent": (224, 104, 108), "sticker": (250, 190, 204), "chip": (252, 214, 160)},
    {"name": "mint green and cream", "bg": ((218, 240, 226), (172, 216, 196)), "accent": (60, 150, 120), "sticker": (190, 232, 210), "chip": (255, 224, 170)},
    {"name": "lavender and light pink", "bg": ((232, 222, 248), (206, 188, 236)), "accent": (130, 90, 190), "sticker": (246, 196, 226), "chip": (255, 226, 180)},
    {"name": "baby blue and soft yellow", "bg": ((214, 234, 250), (176, 208, 238)), "accent": (70, 120, 196), "sticker": (255, 214, 190), "chip": (255, 232, 160)},
]

_PRICE = re.compile(r"[\u20b9$%]|\b(rs\.?|inr|off|free|offer|sale|discount|price|cheap|best|guaranteed|no\.? ?1|number 1|only \d)\b|\d{3,}", re.I)


def _log(*a):
    print("  [cover]", *a)


# ----------------------------------------------------------------------------------------------- fonts
_FC = {}


def _font(kind, size):
    key = (kind, size)
    if key in _FC:
        return _FC[key]
    name = FONT_BOLD if kind == "bold" else FONT_REG
    cands = [os.path.join(FONT_DIR, name), name,
             "C:/Windows/Fonts/segoeuib.ttf" if kind == "bold" else "C:/Windows/Fonts/segoeui.ttf",
             "C:/Windows/Fonts/arialbd.ttf" if kind == "bold" else "C:/Windows/Fonts/arial.ttf",
             "DejaVuSans-Bold.ttf" if kind == "bold" else "DejaVuSans.ttf"]
    f = None
    for c in cands:
        try:
            f = ImageFont.truetype(c, size)
            break
        except Exception:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size)
        except Exception:
            f = ImageFont.load_default()
    _FC[key] = f
    return f


def _tw(draw, text, font):
    return draw.textlength(text, font=font)


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if _tw(draw, t, font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(draw, text, kind, max_w, max_h, max_size, min_size=18, max_lines=2):
    """Biggest font size at which the text fits the box (wrapped, at most max_lines). Never overflows: shrinks below min_size if it must."""
    size = max_size
    while True:
        font = _font(kind, size)
        lines = _wrap(draw, text, font, max_w)
        lh = int(size * 1.12)
        ok = len(lines) <= max_lines and len(lines) * lh <= max_h and all(_tw(draw, l, font) <= max_w for l in lines)
        if ok or size <= 14:
            return lines, font, lh
        size -= 2


def draw_lines(draw, lines, font, lh, cx, top, fill, stroke=None):
    y = top
    for l in lines:
        draw.text((cx - _tw(draw, l, font) / 2, y), l, font=font, fill=fill, stroke_width=(2 if stroke else 0), stroke_fill=stroke)
        y += lh
    return y


# ------------------------------------------------------------------------------------------- text fields
def _ascii_ok(t):
    return bool(re.fullmatch(r"[A-Za-z0-9 &'!?,.\-+/]+", t or ""))


def _check_fields(data):
    """Rules the CODE enforces on the words of the cover. Returns an error string or None."""
    import script_writer
    try:
        headline, tagline = str(data["headline"]).strip(), str(data["tagline"]).strip()
        points, labels, icons = data["points"], data["labels"], data["icons"]
        callout, scene = str(data["callout"]).strip(), str(data["bg_scene"]).strip()
    except Exception:
        return "missing field"
    if not (isinstance(points, list) and len(points) == 4 and isinstance(labels, list) and len(labels) == 4 and isinstance(icons, list) and len(icons) == 4):
        return "points, labels and icons must each have exactly 4 items"
    limits = [(headline, 26, "headline"), (tagline, 34, "tagline"), (callout, 30, "callout")] + \
             [(str(x).strip(), 20, "point") for x in points] + [(str(x).strip(), 24, "label") for x in labels]
    for t, n, what in limits:
        if not t or len(t) > n:
            return "%s must be 1 to %d characters: '%s'" % (what, n, t)
        if not _ascii_ok(t):
            return "%s may use only English letters, digits and simple punctuation: '%s'" % (what, t)
        if _PRICE.search(t):
            return "%s mentions a price, offer or claim: '%s'" % (what, t)
        b = script_writer._banned(t)
        if b:
            return "%s contains the banned word '%s'" % (what, b)
    if any(i not in ICONS for i in icons):
        return "icons must come from " + ", ".join(ICONS)
    if len(scene) < 5 or len(scene) > 160:
        return "bg_scene must be 5 to 160 characters"
    return None


def make_fields(p, script=None, avoid=""):
    """One AI call: all words of the cover, from the product facts. Layout never changes, only these words."""
    import script_writer
    facts = (p.get("description") or "")[:600]
    prompt = (
        "You write the words for an Instagram Reel COVER (a shop-style scrapbook collage) for one product.\n"
        "Product: " + p["name"] + "\nDetails from the product page: " + (facts or "none") + "\n"
        "What the product picture really shows: " + (p.get("visible") or "not available") + "\n"
        "Use ONLY facts from the details above. Plain simple English, friendly, short.\n"
        "NEVER use: price, discount, offer, free, 'best', 'perfect', 'premium', 'quality', 'amazing', 'guaranteed', numbers like 499, % or a currency sign, "
        "claims about health, kids' learning or safety, brand names.\n" + (("Avoid this problem from the last try: " + avoid + "\n") if avoid else "") +
        "Return JSON: {\"headline\": \"the product in 1 to 3 plain words (max 26 characters)\", "
        "\"tagline\": \"short friendly line (max 34 characters)\", "
        "\"points\": [4 very short real selling points, max 20 characters each], "
        "\"callout\": \"short call-out sentence about what it is useful for (max 30 characters)\", "
        "\"labels\": [4 bottom labels for the strip, max 24 characters each, e.g. a gift idea, a real feature, a use], "
        "\"icons\": [4 icons, one per label, each one of: " + ", ".join(ICONS) + "], "
        "\"bg_scene\": \"a soft blurred background place that suits this product category (for example: cosy kitchen counter, pastel kids room shelf, "
        "sunny desk with plants), no product, max 120 characters\"}")

    def validate(t):
        try:
            data = json.loads(t)
        except Exception:
            return "not valid JSON"
        return _check_fields(data) if isinstance(data, dict) else "answer must be one JSON object"

    data = json.loads(llm.generate(prompt, validate=validate))
    for k in ("headline", "tagline", "callout", "bg_scene"):
        data[k] = str(data[k]).strip()
    data["points"] = [str(x).strip() for x in data["points"]]
    data["labels"] = [str(x).strip() for x in data["labels"]]
    return data


def fields_claims_ok(p, data):
    """Fact check the words with the same code the script uses (script_writer.claims_check_many). Returns (ok, unsupported list)."""
    if os.getenv("COVER_FACTCHECK", "1").strip().lower() in ("0", "false", "no", "off"):
        return True, []
    import script_writer
    text = ". ".join([data["headline"], data["tagline"], data["callout"]] + data["points"] + data["labels"])
    try:
        ok, bad = script_writer.claims_check_many(p, {}, [text])[0]
        return bool(ok), list(bad or [])
    except Exception as e:
        _log("claims check could not run (%s) -> the code rules above are used alone" % str(e)[:80])
        return True, []


# ------------------------------------------------------------------------------------------ background
def _grad(pal, seed):
    r = random.Random(seed)
    top, bot = pal["bg"]
    col = Image.new("RGB", (1, H))
    px = col.load()
    for y in range(H):
        t = y / (H - 1)
        px[0, y] = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
    im = col.resize((W, H))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for _ in range(26):                                      # soft bokeh dots: scrapbook feel
        rad = r.randint(30, 120)
        x, y = r.randint(-40, W + 40), r.randint(-40, H + 40)
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(255, 255, 255, r.randint(25, 60)))
    ov = ov.filter(ImageFilter.GaussianBlur(14))
    im = im.convert("RGBA")
    im.alpha_composite(ov)
    return im.convert("RGB")


def background_prompt(scene, pal):
    return ("A soft, blurred, text-free pastel background for a vertical 9:16 social media product cover. Setting: " + scene + ". "
            "Colour palette: " + pal["name"] + ". Warm, cute scrapbook mood with a few small pastel flowers, hearts or sparkles near the corners, gentle bokeh light. "
            "The centre must stay mostly empty and smooth. Absolutely no text, no letters, no numbers, no logos, no watermark, no people, "
            "no product, no picture frames, no borders.")


def make_background(scene, pal, seed):
    """Returns (RGB image 1080x1920, source) where source is 'api' or 'gradient'."""
    prompt = background_prompt(scene, pal)
    if os.getenv("COVER_NO_IMAGE_API", "").strip().lower() not in ("1", "true", "yes", "on"):
        try:
            raw = llm.generate_image(prompt)
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            tw, th = W, int(round(W * im.height / im.width))
            if th < H:                                         # too wide for 9:16: scale by height instead
                th, tw = H, int(round(H * im.width / im.height))
            im = im.resize((tw, th), Image.LANCZOS)
            left, top = (tw - W) // 2, (th - H) // 2
            im = im.crop((left, top, left + W, top + H))
            # lift contrast for text: blend a little cream over the picture
            im = Image.blend(im, Image.new("RGB", (W, H), CREAM), 0.22)
            return im, "api", prompt
        except Exception as e:
            _log("image API gave no background (%s) -> plain pastel gradient instead" % str(e)[:120])
    return _grad(pal, seed), "gradient", prompt


# --------------------------------------------------------------------------------------- product photo
def _focus_box(ref_path):
    """Where the product is inside the photo (AI looks, the code crops). Returns pixel box or the whole image."""
    im = Image.open(ref_path).convert("RGB")
    full = (0, 0, im.width, im.height)
    try:
        buf = io.BytesIO()
        im.copy().resize((min(900, im.width), int(im.height * min(900, im.width) / im.width))).save(buf, "JPEG", quality=85)

        def validate(t):
            try:
                d = json.loads(t)
                x1, y1, x2, y2 = [float(d[k]) for k in ("x1", "y1", "x2", "y2")]
            except Exception:
                return "need numbers x1 y1 x2 y2"
            if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
                return "box must satisfy 0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000"
            return None

        d = json.loads(llm.generate([types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"),
                                     "Give the box that contains the WHOLE main product in this picture (all of it, with a small margin). "
                                     "Coordinates are 0-1000, top-left origin. Return JSON: {\"x1\":..,\"y1\":..,\"x2\":..,\"y2\":..}"],
                                    validate=validate, rounds=1))
        sx, sy = im.width / 1000.0, im.height / 1000.0
        return (int(d["x1"] * sx), int(d["y1"] * sy), int(d["x2"] * sx), int(d["y2"] * sy))
    except Exception as e:
        _log("product box not found (%s) -> whole photo is used" % str(e)[:80])
        return full


def fit_photo(im, bw, bh, focus):
    """The real photo placed in a bw x bh box: cropped around the product (never through it), rest filled with a blurred copy."""
    fx1, fy1, fx2, fy2 = focus
    pad_x, pad_y = (fx2 - fx1) * 0.08, (fy2 - fy1) * 0.08
    fx1, fy1, fx2, fy2 = max(0, fx1 - pad_x), max(0, fy1 - pad_y), min(im.width, fx2 + pad_x), min(im.height, fy2 + pad_y)
    ar = bw / float(bh)
    fw, fh = fx2 - fx1, fy2 - fy1
    cw, ch = (fw, fw / ar) if fw / fh > ar else (fh * ar, fh)
    cw, ch = min(cw, im.width), min(ch, im.height)
    cx, cy = (fx1 + fx2) / 2, (fy1 + fy2) / 2
    x1 = min(max(0, cx - cw / 2), im.width - cw)
    y1 = min(max(0, cy - ch / 2), im.height - ch)
    region = im.crop((int(x1), int(y1), int(x1 + cw), int(y1 + ch)))
    bg = im.copy()
    k = max(bw / bg.width, bh / bg.height)
    bg = bg.resize((int(bg.width * k) + 1, int(bg.height * k) + 1))
    bg = bg.crop(((bg.width - bw) // 2, (bg.height - bh) // 2, (bg.width - bw) // 2 + bw, (bg.height - bh) // 2 + bh))
    bg = Image.blend(bg.filter(ImageFilter.GaussianBlur(22)), Image.new("RGB", (bw, bh), (255, 255, 255)), 0.35)
    s = min(bw / region.width, bh / region.height)
    fg = region.resize((max(1, int(region.width * s)), max(1, int(region.height * s))), Image.LANCZOS)
    bg.paste(fg, ((bw - fg.width) // 2, (bh - fg.height) // 2))
    return bg


# --------------------------------------------------------------------------------------- drawing parts
def _torn_poly(box, amp, seed, step=16):
    x1, y1, x2, y2 = box
    r = random.Random(seed)
    pts = [(x, y1 + r.uniform(-amp, amp)) for x in range(x1, x2, step)]
    pts += [(x2 + r.uniform(-amp, amp), y) for y in range(y1, y2, step)]
    pts += [(x, y2 + r.uniform(-amp, amp)) for x in range(x2, x1, -step)]
    pts += [(x1 + r.uniform(-amp, amp), y) for y in range(y2, y1, -step)]
    return pts


def _layer(w, h):
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _put(canvas, layer, cx, cy, angle=0.0, shadow=True):
    """Paste an RGBA layer centred at (cx, cy), rotated, with a soft drop shadow."""
    if angle:
        layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    x, y = int(cx - layer.width / 2), int(cy - layer.height / 2)
    if shadow:
        a = layer.split()[3].point(lambda v: int(v * 0.30)).filter(ImageFilter.GaussianBlur(9))
        canvas.paste(Image.new("RGB", layer.size, (80, 50, 50)), (x + 5, y + 9), a)
    canvas.paste(layer.convert("RGB"), (x, y), layer.split()[3])


def _tape(layer, x, y, w=120, h=38, angle=-8):
    t = _layer(w + 40, h + 40)
    ImageDraw.Draw(t).rectangle([20, 20, 20 + w, 20 + h], fill=(255, 244, 205, 215))
    t = t.rotate(angle, expand=True, resample=Image.BICUBIC)
    layer.alpha_composite(t, (max(0, int(x - t.width / 2)), max(0, int(y - t.height / 2))))


def photo_card(photo, size, seed, tape=True):
    """White polaroid-style card holding the real photo. size = (w, h) of the card."""
    w, h = size
    pad = 20
    lay = _layer(w + 40, h + 40)
    d = ImageDraw.Draw(lay)
    d.rounded_rectangle([20, 20, 20 + w, 20 + h], radius=26, fill=(255, 255, 255, 255))
    ph = photo.resize((w - 2 * pad, h - 2 * pad))
    m = Image.new("L", ph.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, ph.width - 1, ph.height - 1], radius=16, fill=255)
    lay.paste(ph, (20 + pad, 20 + pad), m)
    if tape:
        _tape(lay, 20 + w * 0.2, 24, angle=-10)
        _tape(lay, 20 + w * 0.82, 24, angle=9)
    return lay


def torn_note(size, fill, seed, amp=5):
    w, h = size
    lay = _layer(w + 40, h + 40)
    ImageDraw.Draw(lay).polygon([(x + 20, y + 20) for x, y in _torn_poly((0, 0, w, h), amp, seed)], fill=fill + (255,))
    return lay


def _icon(d, kind, cx, cy, s, col):
    if kind == "heart":
        d.ellipse([cx - s, cy - s * 0.9, cx, cy + s * 0.1], fill=col)
        d.ellipse([cx, cy - s * 0.9, cx + s, cy + s * 0.1], fill=col)
        d.polygon([(cx - s * 0.97, cy - s * 0.15), (cx + s * 0.97, cy - s * 0.15), (cx, cy + s * 1.0)], fill=col)
    elif kind == "star":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rad = s * (1.05 if i % 2 == 0 else 0.45)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=col)
    elif kind == "diamond":
        d.polygon([(cx - s, cy - s * 0.3), (cx - s * 0.5, cy - s * 0.9), (cx + s * 0.5, cy - s * 0.9), (cx + s, cy - s * 0.3), (cx, cy + s)], fill=col)
    elif kind == "check":
        d.line([(cx - s * 0.8, cy), (cx - s * 0.2, cy + s * 0.6), (cx + s * 0.85, cy - s * 0.6)], fill=col, width=max(6, int(s * 0.3)), joint="curve")
    elif kind == "sparkle":
        pts = []
        for i in range(8):
            a = -math.pi / 2 + i * math.pi / 4
            rad = s * (1.1 if i % 2 == 0 else 0.3)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        d.polygon(pts, fill=col)
    else:                                                   # gift
        d.rectangle([cx - s * 0.85, cy - s * 0.25, cx + s * 0.85, cy + s * 0.85], fill=col)
        d.rectangle([cx - s * 0.95, cy - s * 0.6, cx + s * 0.95, cy - s * 0.2], fill=col)
        d.rectangle([cx - s * 0.12, cy - s * 0.6, cx + s * 0.12, cy + s * 0.85], fill=CREAM)
        d.ellipse([cx - s * 0.6, cy - s * 1.05, cx - s * 0.02, cy - s * 0.55], outline=col, width=max(4, int(s * 0.14)))
        d.ellipse([cx + s * 0.02, cy - s * 1.05, cx + s * 0.6, cy - s * 0.55], outline=col, width=max(4, int(s * 0.14)))


def _arrow(d, p0, p1, ctrl, col=INK, width=6):
    pts = []
    for i in range(31):
        t = i / 30.0
        pts.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t * t * p1[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t * t * p1[1]))
    d.line(pts, fill=col, width=width, joint="curve")
    a = math.atan2(pts[-1][1] - pts[-4][1], pts[-1][0] - pts[-4][0])
    for da in (2.55, -2.55):
        d.line([p1, (p1[0] + 38 * math.cos(a + da), p1[1] + 38 * math.sin(a + da))], fill=col, width=width)


# ------------------------------------------------------------------------------------------- compose
def _crop_variants(im):
    """Two close-ups of the SAME real photo, used when the run has no reference_2/3 pictures."""
    w, h = im.size
    a = im.crop((int(w * 0.2), int(h * 0.15), int(w * 0.8), int(h * 0.85)))
    b = im.crop((int(w * 0.35), int(h * 0.3), w, h))
    return [a, b]


def compose(bg, ref_path, extra_paths, fields, pal, attempt, focus=None):
    r = random.Random(attempt * 7919)
    canvas = bg.copy()
    dr = ImageDraw.Draw(canvas)
    ref = Image.open(ref_path).convert("RGB")
    focus = focus or (0, 0, ref.width, ref.height)

    # small hearts / sparkles (kept clear of the text)
    for (x, y, k, s) in ((120, 520, "heart", 22), (990, 440, "sparkle", 26), (60, 1500, "heart", 20), (1010, 1560, "sparkle", 22)):
        _icon(dr, k, x, y, s, pal["accent"])

    # headline banner: cream rounded, headline (accent colour), tagline chip
    ban = _layer(960, 320)
    bd = ImageDraw.Draw(ban)
    bd.rounded_rectangle([20, 20, 940, 300], radius=70, fill=CREAM + (255,))
    lines, f, lh = fit_text(bd, fields["headline"], "bold", 840, 170, 120, 54, 2)
    top = 20 + (280 - len(lines) * lh) / 2 - 4
    draw_lines(bd, lines, f, lh, 480, top, pal["accent"])
    _put(canvas, ban, 540, 290, 0, shadow=True)
    dr = ImageDraw.Draw(canvas)
    tl, tf, tlh = fit_text(dr, fields["tagline"], "bold", 640, 56, 40, 24, 1)
    tw = int(_tw(dr, tl[0], tf)) + 80
    chip = _layer(tw + 40, 90)
    ImageDraw.Draw(chip).rounded_rectangle([20, 14, 20 + tw, 76], radius=31, fill=pal["chip"] + (255,))
    _put(canvas, chip, 540, 462, 0, shadow=True)
    dr = ImageDraw.Draw(canvas)
    dr.text((540 - _tw(dr, tl[0], tf) / 2, 462 - tlh / 2 - 4), tl[0], font=tf, fill=INK)

    # main product card (left / centre)
    mw, mh = 720, 950
    main = photo_card(fit_photo(ref, mw - 40, mh - 40, focus), (mw, mh), attempt)
    _put(canvas, main, 410, 1000, -2.0)

    # sticker with 4 points (top right)
    st = torn_note((290, 250), pal["sticker"], attempt + 3, amp=4)
    sd = ImageDraw.Draw(st)
    fits = [fit_text(sd, pt, "bold", 215, 40, 30, 17, 1) for pt in fields["points"]]
    fsize = min(ff.size for _, ff, _ in fits)               # one size for all 4 lines
    ff = _font("bold", fsize)
    y = 46
    for pt, _f, _l in zip(fields["points"], range(4), range(4)):
        sd.ellipse([46, y + 13, 58, y + 25], fill=pal["accent"])
        sd.text((72, y), pt, font=ff, fill=INK)
        y += 50
    _tape(st, 284, 14, w=60, h=26, angle=40)
    _put(canvas, st, 905, 620, 4.0)

    # two smaller example tiles (reference_2 / reference_3, else close-ups of the same photo)
    extras = []
    for pth in extra_paths[:2]:
        try:
            extras.append(Image.open(pth).convert("RGB"))
        except Exception:
            pass
    for im in _crop_variants(ref):
        if len(extras) < 2:
            extras.append(im)
    for i, (im, cy) in enumerate(zip(extras[:2], (925, 1235))):
        tile = photo_card(fit_photo(im, 200, 250, (0, 0, im.width, im.height)), (240, 290), attempt + i, tape=False)
        _put(canvas, tile, 910, cy, 3.0 if i == 0 else -3.0)

    # callout (lower right) with a hand-drawn arrow to the product
    cl = torn_note((400, 150), pal["sticker"], attempt + 11, amp=5)
    cd = ImageDraw.Draw(cl)
    cl_lines, cf, clh = fit_text(cd, fields["callout"], "bold", 340, 110, 46, 24, 2)
    draw_lines(cd, cl_lines, cf, clh, 220, 20 + (150 - len(cl_lines) * clh) / 2, INK)
    _tape(cl, 400, 24, w=70, h=30, angle=38)
    _put(canvas, cl, 830, 1515, -3.0)
    ad = ImageDraw.Draw(canvas)
    _arrow(ad, (655, 1545), (545, 1440), (565, 1550))

    # bottom strip: cream torn paper, 4 round icons + labels
    strip = torn_note((1000, 190), CREAM, attempt + 21, amp=6)
    sd = ImageDraw.Draw(strip)
    for i in range(4):
        cx = 20 + 125 + i * 250
        sd.ellipse([cx - 44, 20 + 14, cx + 44, 20 + 102], fill=pal["chip"])
        _icon(sd, fields["icons"][i], cx, 20 + 58, 24, INK)
        ln, ff, lh3 = fit_text(sd, fields["labels"][i], "bold", 226, 62, 30, 18, 2)
        draw_lines(sd, ln, ff, lh3, cx, 20 + 112, INK)
    _put(canvas, strip, 540, 1695, 0)
    return canvas


# ------------------------------------------------------------------------------------------ verification
def local_checks(im):
    """Free checks first: 9:16 size, not blank, not all dark or all white. Returns an error string or None."""
    if im.size != (W, H):
        return "wrong size %dx%d (need %dx%d)" % (im.width, im.height, W, H)
    st = ImageStat.Stat(im.convert("L"))
    if st.stddev[0] < 12:
        return "the cover looks blank"
    if not 40 <= st.mean[0] <= 245:
        return "the cover is far too dark or too bright (mean %d)" % st.mean[0]
    return None


def verify_cover(cover_path, ref_path, fields):
    """Returns (ok, reason, checks). Local checks first, then one AI look (same style as verify_video.check_video)."""
    im = Image.open(cover_path).convert("RGB")
    err = local_checks(im)
    if err:
        return False, err, {"local": False}
    ref = Image.open(ref_path).convert("RGB")
    rw = 420
    ref_s = ref.resize((rw, max(1, int(ref.height * rw / ref.width))))
    cv = im.resize((540, 960))
    canvas = Image.new("RGB", (rw + 540 + 20, max(960, ref_s.height)), (30, 30, 30))
    canvas.paste(ref_s, (0, 0))
    canvas.paste(cv, (rw + 20, 0))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    txt = {k: fields[k] for k in ("headline", "tagline", "points", "callout", "labels")}
    prompt = (
        "You quality-check an Instagram Reel cover. ONE image: on the LEFT the real product photo, on the RIGHT the finished cover.\n"
        "The words that must appear on the cover are exactly: " + json.dumps(txt, ensure_ascii=False) + "\n"
        "Answer as JSON: {\"product_matches\": true/false (the product on the cover is the same product as the real photo: same shape, colours, parts), "
        "\"text_readable\": true/false (all text is sharp, not cut off, not overlapping other text or hidden behind pictures, easy to read on a phone), "
        "\"text_matches\": true/false (the words on the cover equal the words above, no extra, garbled or duplicated text), "
        "\"no_price_or_claims\": true/false (no price, discount, 'best' or 'guaranteed' anywhere), "
        "\"edges_ok\": true/false (nothing important touches or is cut by the outer edges), "
        "\"reason\": \"if anything is wrong say what, else 'ok'\"}")
    keys = ["product_matches", "text_readable", "text_matches", "no_price_or_claims", "edges_ok"]

    def validate(t):
        try:
            d = json.loads(t)
        except Exception:
            return "not valid JSON"
        for k in keys:
            if not isinstance(d.get(k), bool):
                return "'%s' must be true or false" % k
        return None

    d = json.loads(llm.generate([types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), prompt], validate=validate))
    checks = {k: d[k] for k in keys}
    failed = [k for k, v in checks.items() if not v]
    return (not failed), ("ok" if not failed else "failed: " + ", ".join(failed) + ". " + str(d.get("reason", ""))), checks


# -------------------------------------------------------------------------------------------- the job
def _write_prompt_file(out_dir, info):
    try:
        with open(os.path.join(out_dir, "cover_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(info)
    except Exception as e:
        _log("cover_prompt.txt not written:", str(e)[:80])


def _ref_files(out_dir):
    """The product pictures of the run. NEVER product.png: that is a screenshot of the shopping app."""
    main = os.path.join(out_dir, "reference.png")
    extra = sorted(glob.glob(os.path.join(out_dir, "reference_*.png")))
    return (main if os.path.exists(main) else (extra.pop(0) if extra else None)), extra


def build_cover(out_dir, p, script=None, attempts=None):
    attempts = attempts or ATTEMPTS
    res = {"ok": False, "path": None, "reason": "", "attempts": 0}
    ref, extras = _ref_files(out_dir)
    if not ref:
        res["reason"] = "no reference.png in the run folder"
        return res
    log = ["COVER PROMPT FILE", "Product: " + p["name"], ""]
    try:
        fields, avoid = None, ""
        for t in range(3):                                   # words: code rules + fact check, up to 3 tries
            fields = make_fields(p, script, avoid)
            ok, bad = fields_claims_ok(p, fields)
            if ok:
                break
            avoid = "these claims are not supported by the product page: " + "; ".join(bad)[:300]
            _log("words rejected by the fact check:", avoid[:160])
            fields = None
        if not fields:
            res["reason"] = "could not write cover words that pass the fact check"
            return res
        log += ["TEXT FIELDS (drawn by Pillow, never by the image model):", json.dumps({k: fields[k] for k in fields if k != "bg_scene"}, ensure_ascii=False, indent=2), "",
                "BACKGROUND SCENE: " + fields["bg_scene"], ""]
        focus = _focus_box(ref)
        last_path = os.path.join(out_dir, "cover_failed_last.png")
        why = ""
        for a in range(1, attempts + 1):
            res["attempts"] = a
            pal = PALETTES[(a - 1) % len(PALETTES)]
            _log("attempt %d/%d (%s)" % (a, attempts, pal["name"]))
            bg, src, bprompt = make_background(fields["bg_scene"], pal, a)
            log += ["ATTEMPT %d  palette: %s  background source: %s" % (a, pal["name"], src), "Background prompt: " + bprompt]
            im = compose(bg, ref, extras, fields, pal, a, focus)
            tmp = os.path.join(out_dir, "cover_try%d.png" % a)
            im.save(tmp)
            try:
                ok, why, checks = verify_cover(tmp, ref, fields)
            except Exception as e:                           # AI unavailable: unverified covers are used only if the .env says so
                if os.getenv("COVER_ALLOW_UNVERIFIED", "0").strip().lower() in ("1", "true", "yes", "on") and not local_checks(im):
                    ok, why, checks = True, "AI check could not run (%s); allowed by COVER_ALLOW_UNVERIFIED" % str(e)[:60], {}
                else:
                    ok, why, checks = False, "AI check could not run: " + str(e)[:100], {}
            log += ["Check: %s - %s %s" % ("PASS" if ok else "FAIL", why, json.dumps(checks)), ""]
            if ok:
                final = os.path.join(out_dir, "cover.png")
                os.replace(tmp, final)
                res.update(ok=True, path=final, reason=why)
                break
            _log("check failed:", why)
            os.replace(tmp, last_path)
        else:
            res["reason"] = "cover failed verification %d times: %s" % (attempts, why)
        for f in glob.glob(os.path.join(out_dir, "cover_try*.png")):
            try:
                os.remove(f)
            except Exception:
                pass
    except Exception as e:
        res["reason"] = "cover could not be built: " + str(e)[:200]
        log.append("ERROR: " + res["reason"])
    log.append("RESULT: " + ("OK " + str(res["path"]) if res["ok"] else "NO COVER - " + res["reason"]))
    _write_prompt_file(out_dir, "\n".join(log))
    return res


class CoverJob(threading.Thread):
    def __init__(self, out_dir, p, script):
        super().__init__(daemon=True)
        self.out_dir, self.p, self.script = out_dir, p, script
        self.result = {"ok": False, "path": None, "reason": "not finished", "attempts": 0}

    def run(self):
        try:
            self.result = build_cover(self.out_dir, self.p, self.script)
        except Exception as e:
            self.result = {"ok": False, "path": None, "reason": "cover crashed: " + str(e)[:150], "attempts": 0}


def enabled():
    return os.getenv("COVER_STEP", "1").strip().lower() not in ("0", "false", "no", "off")


def start(out_dir, p, script=None):
    """Start building in the background (call as soon as the product details are known). Returns the job or None when switched off."""
    if not enabled():
        return None
    job = CoverJob(out_dir, p, script)
    job.start()
    print("  [cover] cover is being made in the background...")
    return job


def wait(job, timeout=900):
    """Wait until the cover is finished AND verified. Never raises. Returns the result dict."""
    if job is None:
        return {"ok": False, "path": None, "reason": "cover step switched off", "attempts": 0}
    job.join(timeout)
    if job.is_alive():
        return {"ok": False, "path": None, "reason": "cover took too long", "attempts": 0}
    r = job.result
    print("  [cover] %s - %s" % ("READY" if r["ok"] else "NO COVER (Instagram's default will be used)", r.get("reason", "")))
    return r
