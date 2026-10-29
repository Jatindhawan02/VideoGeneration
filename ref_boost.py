"""ref_boost.py - gives YouTube Create more than one reference picture (it accepts up to 3).

choose_references() only takes gallery pictures that are clean product shots. Many products have just one of those, the rest are
infographics, pictures with added text, or a person holding the item. This module tries to turn those into extra references:

  1. For each remaining gallery picture the AI says WHERE the product is, as a crop box that leaves out added text, price tags,
     logos and any human face.                                         (the AI gives a box, the CODE does the cropping)
  2. The crop is checked in code: big enough (YouTube Create needs a sharp picture), not a near-copy of a reference already chosen.
  3. The AI compares the crop with the MAIN reference: same product (shape, colour, print), whole product visible, no text, no face.
  Only crops that pass all three become reference_2.png / reference_3.png. If none pass, nothing changes (the run goes on with what it had).

    ref_files = ref_boost.boost(carousel, cls, picked, ref_files, out_dir, crop_fn=crop_product)

.env: REF_BOOST=1 (0 = off), REF_MAX=3, REF_MIN_SIDE=320 (smallest side of a crop in pixels), REF_TRIES=4 (gallery pictures looked at).
"""
import io, json, math, os
from PIL import Image
from google.genai import types
import llm

MIN_SIDE = int(os.getenv("REF_MIN_SIDE", "320"))
MAX_TRIES = int(os.getenv("REF_TRIES", "4"))


def enabled():
    return os.getenv("REF_BOOST", "1").strip().lower() not in ("0", "false", "no", "off")


def _jpeg(im, width=900):
    im = im.convert("RGB")
    if im.width > width:
        im = im.resize((width, max(1, int(im.height * width / im.width))))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    return b.getvalue()


def _corr(a, b, size=(24, 24)):
    """Similarity of two pictures, -1..1 (grey, tiny). Above ~0.97 = practically the same picture."""
    x, y = list(a.convert("L").resize(size).tobytes()), list(b.convert("L").resize(size).tobytes())
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx, syy = sum((i - mx) ** 2 for i in x), sum((j - my) ** 2 for j in y)
    if sxx < 1 or syy < 1:
        return 1.0 if abs(mx - my) < 8 else 0.0
    return sum((i - mx) * (j - my) for i, j in zip(x, y)) / math.sqrt(sxx * syy)


def _order(carousel, cls, taken):
    """Gallery pictures not used yet, best candidates first: product shots with text, then infographics/other, then people. Never size charts."""
    rank = {"product": 0, "infographic": 1, "other": 1, "model": 2}
    rows = []
    for i in range(min(len(carousel), 6)):
        if i in taken:
            continue
        c = (cls or {}).get(i + 1) or {"type": "other"}
        t = c.get("type", "other")
        if t == "size_chart":
            continue
        rows.append((rank.get(t, 1), i, t))
    rows.sort()
    return [(i, t) for _, i, t in rows]


def _find_box(im):
    """AI: the crop that holds the whole product and nothing that must not be in a reference. Returns a pixel box or None."""
    prompt = (
        "This is a picture from a shopping app's gallery for ONE product. I want to cut out a clean reference picture of the product.\n"
        "Find ONE rectangle that contains the WHOLE physical product (not cut off) and as little else as possible, and that contains "
        "NO added text, price, offer badge, logo, watermark, arrow, icon, size table and NO human face.\n"
        "If no such rectangle exists (added text covers the product, the product is cut off or tiny, or it only appears on a person's face), "
        "say possible=false. Coordinates are 0-1000, top-left origin.\n"
        "Return JSON: {\"possible\": true/false, \"x1\": 0, \"y1\": 0, \"x2\": 1000, \"y2\": 1000, \"reason\": \"short\"}")

    def validate(t):
        try:
            d = json.loads(t)
        except Exception:
            return "not valid JSON"
        if not isinstance(d.get("possible"), bool):
            return "'possible' must be true or false"
        if d["possible"]:
            try:
                x1, y1, x2, y2 = [float(d[k]) for k in ("x1", "y1", "x2", "y2")]
            except Exception:
                return "need numbers x1 y1 x2 y2"
            if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
                return "box must satisfy 0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000"
        return None

    d = json.loads(llm.generate([types.Part.from_bytes(data=_jpeg(im), mime_type="image/jpeg"), prompt], validate=validate, rounds=1))
    if not d.get("possible"):
        print("    no clean crop possible (%s)" % str(d.get("reason", ""))[:80])
        return None
    pad = 0.03                                               # a little air around the product
    x1, y1, x2, y2 = [float(d[k]) for k in ("x1", "y1", "x2", "y2")]
    w, h = im.size
    bx1, by1 = max(0, (x1 - pad * 1000) / 1000.0 * w), max(0, (y1 - pad * 1000) / 1000.0 * h)
    bx2, by2 = min(w, (x2 + pad * 1000) / 1000.0 * w), min(h, (y2 + pad * 1000) / 1000.0 * h)
    return int(bx1), int(by1), int(bx2), int(by2)


def _same_product(main_ref, crop):
    """AI: is the crop the SAME product as the main reference, and clean? Returns (ok, reason)."""
    mw = 420
    a = main_ref.convert("RGB").resize((mw, max(1, int(main_ref.height * mw / main_ref.width))))
    b = crop.convert("RGB").resize((mw, max(1, int(crop.height * mw / crop.width))))
    canvas = Image.new("RGB", (mw * 2 + 16, max(a.height, b.height)), (40, 40, 40))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (mw + 16, 0))
    keys = ["same_product", "product_complete", "text_or_logo_visible", "face_visible"]
    prompt = (
        "LEFT: the MAIN reference picture of a product. RIGHT: a new candidate reference cut from another gallery picture.\n"
        "Answer as JSON: {\"same_product\": true/false (the right picture shows the SAME item as the left: same shape, same colours, same print or design; "
        "a different angle or background is fine, a different colour or design is NOT), "
        "\"product_complete\": true/false (the whole product is visible on the right, nothing important cut off, and it is large and sharp enough), "
        "\"text_or_logo_visible\": true/false (any added text, price, badge, logo, watermark or icon on the right; print that is part of the product itself does not count), "
        "\"face_visible\": true/false (a human face is visible on the right), "
        "\"reason\": \"short\"}")

    def validate(t):
        try:
            d = json.loads(t)
        except Exception:
            return "not valid JSON"
        for k in keys:
            if not isinstance(d.get(k), bool):
                return "'%s' must be true or false" % k
        return None

    d = json.loads(llm.generate([types.Part.from_bytes(data=_jpeg(canvas, 1000), mime_type="image/jpeg"), prompt], validate=validate, rounds=1))
    ok = d["same_product"] and d["product_complete"] and not d["text_or_logo_visible"] and not d["face_visible"]
    return ok, str(d.get("reason", ""))[:100]


def boost(carousel, cls, picked, ref_files, out_dir, crop_fn=None, max_refs=None):
    """Returns the new list of reference files (the old ones first, then the extra crops). Never raises, never removes a reference."""
    max_refs = max_refs or int(os.getenv("REF_MAX", "3"))
    out = list(ref_files)
    if not enabled() or not out or len(out) >= max_refs:
        return out
    try:
        main_ref = Image.open(out[0]).convert("RGB")
        have = [Image.open(f).convert("RGB") for f in out]
    except Exception as e:
        print("  (extra references skipped: %s)" % str(e)[:80])
        return out
    print("  only %d reference picture(s) so far -> trying to make up to %d by cropping other gallery pictures..." % (len(out), max_refs))
    tried = 0
    for i, kind in _order(carousel, cls, set(picked)):
        if len(out) >= max_refs or tried >= MAX_TRIES:
            break
        tried += 1
        print("   gallery picture %d (%s):" % (i + 1, kind))
        try:
            base = Image.open(carousel[i][0]).convert("RGB")
            if crop_fn:
                base = crop_fn(base)
            box = _find_box(base)
            if not box:
                continue
            crop = base.crop(box)
            if min(crop.size) < MIN_SIDE:
                print("    crop too small (%dx%d, need %d px on the short side)" % (crop.width, crop.height, MIN_SIDE))
                continue
            if any(_corr(crop, h) > 0.97 for h in have):
                print("    practically the same picture as one already chosen")
                continue
            ok, why = _same_product(main_ref, crop)
            if not ok:
                print("    rejected by the check (%s)" % why)
                continue
            path = os.path.join(out_dir, "reference.png" if not out else "reference_%d.png" % (len(out) + 1))
            crop.save(path)
            out.append(path)
            have.append(crop)
            print("    OK -> %s (%dx%d, same product, no text, no face)" % (os.path.basename(path), crop.width, crop.height))
        except Exception as e:
            print("    skipped (%s)" % str(e)[:100])
    print("  reference pictures for the video: %d" % len(out))
    return out
