"""Does the finished video show the SAME product as the reference photos?

Looks at the WHOLE video (8 frames in time order, all in one picture) next to ALL reference pictures, and checks every item of a combo\n(for example earbuds + watch) one by one and rejects videos that merge the items.
The old check compared one reference photo with one frame, so a combo video that showed only the earbuds in its first
frame was judged "different product".

check(video, ref_paths, product, loose=False) -> (True / False / None, reason). None = could not be checked (not a failure).
loose=True is for animation formats (claymation, cartoon product, sung jingle): the video is deliberately stylised, so the judge
compares overall shape, main colours and key parts only, and tiny eyes / a mouth / bouncing movement are NOT "an invented feature".
Needs ffmpeg on PATH (same as verify_video).
"""
import io, json, tempfile
from PIL import Image, ImageDraw, ImageFont
from google.genai import types
import llm
import verify_video

LOOSE_RULE = (
    "SPECIAL CASE: this video is DELIBERATELY a cartoon / claymation / 3D animation of the product. Stylised textures, soft clay or cartoon "
    "shading, tiny eyes or a mouth, bouncing, wiggling, flipping and sparkles are expected and are NOT an invented feature. "
    "same_design = true when the animated object has the same overall shape, the same main colours and the same key parts "
    "as in Part A. fused = true ONLY if separate items are merged into one object or a real part is added or removed (a different "
    "number of items, a missing or extra major part).\n")

W = 1600   # bigger picture: small details (case, bracelet) stay visible


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


def _bar(text):
    im = Image.new("RGB", (W, 40), (30, 30, 30))
    ImageDraw.Draw(im).text((12, 4), text, fill=(255, 255, 255), font=_font(28))
    return im


def _board(ref_paths, frames):
    """ONE picture: top = the reference pictures, bottom = the video frames in time order."""
    refs = []
    for p in ref_paths[:3]:
        im = Image.open(p).convert("RGB")
        refs.append(im.resize((max(1, int(im.width * 360 / im.height)), 360)))
    total = sum(r.width for r in refs)
    if total > W:
        f = W / float(total)
        refs = [r.resize((max(1, int(r.width * f)), max(1, int(r.height * f)))) for r in refs]
    top_h = max(r.height for r in refs)
    top = Image.new("RGB", (W, top_h), (255, 255, 255))
    x = 0
    for r in refs:
        top.paste(r, (x, 0))
        x += r.width
    vid = Image.open(io.BytesIO(verify_video._collage(frames, cols=4, tw=W // 4))).convert("RGB")
    canvas = Image.new("RGB", (W, 40 + top_h + 40 + vid.height), (255, 255, 255))
    y = 0
    for part in (_bar("A: REFERENCE PHOTOS OF THE PRODUCT"), top, _bar("B: FRAMES OF THE VIDEO (time order, left to right, top to bottom)"), vid):
        canvas.paste(part, (0, y))
        y += part.height
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _b(v):
    return (v.strip().lower() in ("true", "yes")) if isinstance(v, str) else bool(v)


def check(video, ref_paths, product, loose=False):
    try:
        dur = verify_video._duration(video)
        with tempfile.TemporaryDirectory() as tmp:
            frames = verify_video._frames(video, tmp, dur)
        if len(frames) < 3 or not ref_paths:
            return None, "could not read the frames or the reference pictures"
        board = _board(ref_paths, frames)
    except Exception as e:
        return None, "product match could not run: " + str(e)[:100]
    prompt = (
        "A short product video was made for this product:\n"
        "NAME: " + product["name"] + "\nPAGE DETAILS: " + (product.get("description") or "none") + "\n"
        + (("WHAT THE PRODUCT PHOTO SHOWS: " + product["visible"] + "\n") if product.get("visible") else "") +
        "You get ONE image. Part A (top) = reference photo(s) of the product. Part B (bottom) = frames of the video in time order.\n\n"
        "STEP 1. List every SEPARATE physical item the buyer receives, using Part A (a combo has several, "
        "e.g. 'black silicone earbuds case', 'silver wristwatch with blue flower bracelet').\n"
        "STEP 2. For EACH item, look at ALL frames in part B and answer:\n"
        "  shown       = clearly visible in at least one frame\n"
        "  same_design = same kind of object, same main colour and overall shape as in Part A "
        "(tiny logo or pattern changes are fine; a different-looking object is not)\n"
        "  separate    = appears as its own independent object, not built into another item\n"
        "STEP 3. fused = true if the video merges items into one object (for example earbuds hidden inside a watch, "
        "a watch that opens like a case) or gives the product a feature or shape that Part A does not show. "
        "For a single-item product, fused = true only if the video invents such a feature.\n"
        "A person wearing or holding the items is fine.\n"
        + (LOOSE_RULE if loose else "") +
        "Return JSON only: {\"items\": [{\"name\": \"...\", \"shown\": true, \"same_design\": true, \"separate\": true}], "
        "\"fused\": false, \"reason\": \"one short sentence\"}"
    )

    def validate(t):
        try:
            d = json.loads(t)
            if not isinstance(d.get("items"), list) or not d["items"]:
                return "items must be a non-empty list"
            if "fused" not in d or "reason" not in d:
                return "needs fused and reason"
            for it in d["items"]:
                if not isinstance(it, dict) or not all(k in it for k in ("name", "shown", "same_design", "separate")):
                    return "each item needs name, shown, same_design, separate"
        except Exception:
            return "not valid JSON"
        return None
    try:
        part = types.Part.from_bytes(data=board, mime_type="image/jpeg")
        d = json.loads(llm.generate([part, prompt], validate=validate))
    except Exception as e:
        return None, "product match could not run: " + str(e)[:100]

    problems = []
    for it in d["items"]:
        n = str(it.get("name", "item"))
        if not _b(it.get("shown")):
            problems.append("'%s' never appears in the video" % n)
        elif not _b(it.get("same_design")):
            problems.append("'%s' looks different from the reference photo" % n)
        elif not _b(it.get("separate")):
            problems.append("'%s' is not shown as a separate object" % n)
    if _b(d.get("fused")):
        problems.append("the video MERGED the items into one object or invented a feature")
    names = ", ".join(str(it.get("name", "")) for it in d["items"])
    why = str(d.get("reason", "")).strip() + " [items: " + names + "]"
    if problems:
        return False, "; ".join(problems) + ". " + why
    return True, why
