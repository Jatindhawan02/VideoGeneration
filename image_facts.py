"""image_facts.py - reads the TEXT and the facts printed on the product's own pictures (spec infographics, "what's in the box",
size charts, feature lists) so the script is written from what the pictures say, not only from the short page text.

p["image_text"] is filled once and then used by script_writer (to write the script) and factcheck (as a trusted source).
The pictures are found in p under any of these keys (list of file paths or of image bytes):
    gallery, gallery_paths, ref_paths, refs, reference_images, images, shots
If main_link.py keeps the product pictures in another variable, add one line before make_script(p):  p["gallery"] = <that list>
"""
import json, os, re

KEYS = ("gallery", "gallery_paths", "ref_paths", "refs", "reference_images", "images", "shots")
MAX_IMAGES = int(os.getenv("IMAGE_FACTS_MAX", "5"))


def _load(item):
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)
    if isinstance(item, str) and os.path.exists(item):
        with open(item, "rb") as f:
            return f.read()
    return None


def _shrink(data, side=1280):
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((side, side))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=85)
        return b.getvalue()
    except Exception:
        return data


def pictures(p):
    out = []
    for k in KEYS:
        v = p.get(k)
        if isinstance(v, (list, tuple)):
            for it in v:
                d = _load(it)
                if d:
                    out.append(_shrink(d))
        if out:
            break
    return out[:MAX_IMAGES]


def read(p):
    """Fills and returns p["image_text"] ('' if there are no pictures or the AI is busy)."""
    if "image_text" in p:
        return p["image_text"]
    p["image_text"] = ""
    imgs = pictures(p)
    if not imgs:
        print("  [images] no product pictures were handed to the script writer -> script uses page text only "
              "(set p['gallery'] = list of picture paths in main_link.py)")
        return ""
    try:
        import llm
        from google.genai import types
        parts = [types.Part.from_bytes(data=d, mime_type="image/jpeg") for d in imgs]
        prompt = ("These are the product's own pictures from a shopping page. Read ALL the text printed on them (specification tables, "
                  "feature lists, what is in the box, sizes, quantities, materials, charging type, ranges, hours) and note what is visible "
                  "(colours, prints, items shown). Copy numbers and words EXACTLY as printed. Do NOT guess or add anything that is not "
                  "printed or visible. Ignore watermarks, seller codes, URLs and QR codes.\n"
                  "Return JSON: {\"printed_text\": \"all the useful printed facts, e.g. 'Bluetooth Range 10m; Charging Type Micro USB; Standby 48Hrs'\", "
                  "\"visible\": \"short description of what the pictures show\"}")

        def validate(t):
            try:
                j = json.loads(t)
                return None if isinstance(j, dict) and "printed_text" in j else "need printed_text"
            except Exception:
                return "not valid JSON"
        j = json.loads(llm.generate([prompt] + parts, validate=validate))
        txt = " ; ".join(x for x in (str(j.get("printed_text") or "").strip(), str(j.get("visible") or "").strip()) if x)
        p["image_text"] = txt[:2500]
        print("  [images] read %d picture(s): %s" % (len(imgs), p["image_text"][:200]))
    except Exception as e:
        print("  [images] could not read the pictures (%s) -> page text only" % str(e)[:100])
    return p["image_text"]
