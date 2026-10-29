"""factcheck.py - the fact checker for video scripts and product facts.

WHY THE OLD CHECKER (script_writer.claims_check_many) FAILED
  1. It accepted a claim when the AI's own "research facts" supported it, but those facts were written by the same AI,
     so an invented "Type-C charging" supported itself.
  2. When the AI check could not run, it accepted EVERYTHING.
  3. It believed the AI's yes/no without any proof.
  4. It saw only the first 1200 characters of the page text and never the full product title (the page link holds it).

HOW IT WORKS NOW
  A. CODE checks first (no AI, no quota): every number, spec word (Type-C, battery, waterproof, touch, mic, ...),
     colour and material in the text must literally appear in the sources.
  B. AI check second, but it must COPY the exact words from the sources that prove each claim, and the code verifies that
     the copied words really exist. A missing or invented quote = unsupported claim. The AI's yes/no is not trusted.
  C. If the AI cannot run, the code checks still apply and the result is marked ai=False (never silently accepted as checked).
SOURCES: product title, the full title taken from the page link, page details, the raw app-screen text, the photo description.
The AI's own research notes are NOT a source.

check_texts(p, texts, gen=None) -> list of {"ok": bool, "unsupported": [str], "ai": bool}, one per text.
Try it:  python factcheck.py "text to check" "product name" ["details"] ["page text"] ["page link"] [--no-ai]
"""
import json, re, sys


def _n(t):
    """Lower-case words only (Latin, digits, Devanagari), single spaces. Used to compare quotes with sources."""
    t = (t or "").lower().replace("’", "'")
    return re.sub(r"[^a-z0-9ऀ-ॿ]+", " ", t).strip()


# ------------------------------------------------------------------ sources ----
def page_url(p):
    """The product link: p["url"] if the caller gave it, else the Meesho link given on the command line."""
    u = (p.get("url") or "").strip()
    if u:
        return u
    for a in sys.argv[1:]:
        if a.startswith("http") and "/p/" in a:
            return a
    return ""


def url_title(url):
    """Meesho links contain the FULL product title: .../stylish-true-wireless-...-touch-sensor-51-with-hd-mic/p/abc123"""
    m = re.search(r"https?://[^/]+/([^/?#]+)/p/", url or "")
    return m.group(1).replace("-", " ").replace("_", " ") if m else ""


def sources(p):
    """(named parts, raw joined text, normalized joined text)"""
    parts = {
        "PRODUCT TITLE": p.get("name") or "",
        "FULL TITLE FROM THE PAGE LINK": url_title(page_url(p)),
        "PAGE DETAILS": p.get("description") or "",
        "APP SCREEN TEXT": (p.get("page_text") or "")[:6000],
        "PHOTO DESCRIPTION": p.get("visible") or "",
        "TEXT PRINTED ON THE PRODUCT PICTURES": p.get("image_text") or "",
        # only set when "Mention the price" is on AND the price was read from the page and verified (main_link.get_product)
        "VERIFIED SELLING PRICE ON THE PAGE": ("The selling price is Rs %s (%s rupees)" % (p["price"], p["price"])) if str(p.get("price") or "").strip().isdigit() else "",
    }
    parts = {k: v.strip() for k, v in parts.items() if v and v.strip()}
    raw = " . ".join(parts.values())
    return parts, raw, _n(raw)


# --------------------------------------------------------- code checks (no AI) ----
# If the TEXT mentions one of these, the SOURCES must mention it too.
_SPEC = [
    ("Type-C", r"type[\s-]?c\b|usb[\s-]?c\b"),
    ("Micro USB", r"micro[\s-]?usb"),
    ("USB", r"\busb\b"),
    ("battery / charging", r"batter\w*|\bmah\b|charg\w*|play[\s-]?time|standby"),
    ("waterproof / sweatproof", r"water[\s-]?proof|water[\s-]?resist\w*|sweat[\s-]?proof|splash[\s-]?proof|\bipx?\d*\b"),
    ("noise cancelling", r"noise|cancell?ation|\banc\b|\benc\b"),
    ("bass", r"\bbass\b"),
    ("touch control", r"\btouch\b"),
    ("microphone", r"\bmic\b|microphone"),
    ("bluetooth", r"bluetooth"),
    ("gaming / low latency", r"gaming|latency"),
    ("warranty / original / genuine", r"warrant\w*|original|genuine|authentic|certified|guarantee\w*|branded"),
    ("memory / storage", r"\bram\b|storage|\b\d+\s?gb\b"),
    ("display / camera / HD", r"\bamoled\b|\blcd\b|\bled\b|camera|megapixel|\bhd\b|\b4k\b|\bfhd\b"),
    ("smart features", r"\bsmart\b|heart rate|spo2|step count|\bgps\b|\bnfc\b"),
    ("remote / app control", r"\bremote\b|\bapp\b"),
]
_COLOR_MATERIAL = re.compile(
    r"\b(red|blue|green|yellow|pink|black|white|orange|purple|violet|grey|gray|brown|golden|gold|silver|maroon|navy|beige|"
    r"cream|peach|teal|mustard|cyan|magenta|multicolou?r(?:ed)?|rainbow|transparent|cotton|silk|leather|steel|stainless|"
    r"silicone|plastic|wood|wooden|glass|ceramic|metal|brass|copper|velvet|denim|linen|polyester|nylon|wool|woollen|woolen|"
    r"rubber|bamboo|acrylic|cloth|fabric|plush|fur|crystals?|diamond|alloy|jute|paper|foam|clay|marble|stone|pearls?)\b", re.I)
_ALIASES = [("gold", "golden"), ("grey", "gray"), ("crystal", "crystals"), ("pearl", "pearls"), ("wood", "wooden"),
            ("wool", "woollen", "woolen"), ("steel", "stainless"),
            ("multicolour", "multicolor", "multicoloured", "multicolored", "multi colour", "multi color")]
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def _lower(t):
    return (t or "").lower().replace("’", "'")


def _present(word, src_n):
    w = word.lower()
    group = next((g for g in _ALIASES if w in g), (w,))
    hay = " " + src_n + " "
    return any((" " + a + " ") in hay for a in group)


def local_issues(text, raw, src_n):
    """Problems found by CODE alone. Empty list = nothing wrong found (not proof that the text is right)."""
    issues, low, src_low = [], _lower(text), _lower(raw)
    src_nums = set(m.replace(".", "") for m in _NUM.findall(src_low))
    for m in _NUM.findall(low):
        if m.replace(".", "") not in src_nums:
            issues.append("the number %s (not on the page)" % m)
    for label, rx in _SPEC:
        if re.search(rx, low) and not re.search(rx, src_low):
            issues.append(label + " (not on the page)")
    seen = set()
    for m in _COLOR_MATERIAL.finditer(low):
        w = m.group(1)
        if w in seen:
            continue
        seen.add(w)
        if not _present(w, src_n):
            issues.append("'%s' (colour/material not on the page or in the photo description)" % w)
    return issues


# ------------------------------------------------------- AI check with proof ----
_STOP = set("this that with have from your their about into when where what which there these those also only very more some "
            "each other than then them they will would could should been being were does did made make makes".split())


def _quote_found(quote, src_n):
    q = _n(quote)
    return len(q) >= 3 and (" " + q + " ") in (" " + src_n + " ")


# Words that mean the same thing on a spec line ("Product Height: 1.5 Cm" proves "1.5 cm thick" for a flat item).
_SYN = [("thickness", "thick", "height", "tall", "depth"), ("length", "long", "size", "dimension", "dimensions"),
        ("width", "wide", "breadth"), ("weight", "heavy", "gram", "grams", "kilogram", "kg"),
        ("quantity", "pack", "pcs", "pieces", "pair", "count"), ("colour", "color")]
_UNIT_RX = re.compile(r"\b(cm|mm|kg|gm|ml|ltr|inch|mah|gb|pcs|pc)\b")


def _numbers(t):
    return [m.replace(".", "") for m in _NUM.findall(_lower(t))]


def _related(claim, quote):
    """The copied words must share at least one real word with the claim (stops a random real quote from 'proving' it).
    A measurement claim ('1.5 cm thick') is related when its number and unit really appear in the quote, because the
    page often writes the label and the value as 'Product Height' / '1.5' / 'Cm'."""
    cn = _numbers(claim)
    if cn and set(cn) <= set(_numbers(quote)) and set(_UNIT_RX.findall(_lower(claim))) <= set(_n(quote).split()):
        return True
    cw = [w for w in _n(claim).split() if len(w) >= 4 and w not in _STOP]
    qw = set(_n(quote).split())

    def hit(w):
        group = set(next((g for g in _SYN if w in g), ())) | {w}
        return any(g in qw or any(g.startswith(x) or x.startswith(g) for x in qw if len(x) >= 4) for g in group)
    return not cw or any(hit(w) for w in cw)


def _common_sense_review(parts, items, gen):
    """items: list of (text index, claim, why it failed). Before a claim is thrown away, a person-like AI judge decides
    whether it is plain common sense for THIS product. Returns one (keep, why) per item."""
    src = "\n".join("[%s]: %s" % (k, v) for k, v in parts.items())
    listing = "\n".join("%d) %s   (no proof found: %s)" % (n + 1, it[1], it[2]) for n, it in enumerate(items))
    prompt = (
        "A strict checker found no word-for-word proof for the claims below about a product. Before they are thrown away, "
        "judge them with COMMON SENSE, like a normal shopper who sees the product's title, photo description and page.\n"
        + src + "\n\nCLAIMS:\n" + listing + "\n\n"
        "keep = a normal person would agree it is true of THIS product because:\n"
        "  (a) it is true of virtually EVERY product of this kind: what it is for and the ordinary way it is used, or\n"
        "  (b) it follows plainly from something the sources or the photo description show (for example a visible hole can hold a hook, "
        "a handle can be gripped), or\n"
        "  (c) it is only everyday wording or storytelling, not a claim about the product.\n"
        "drop = a buyer could be misled: it states a size, number, colour, material, quality (smooth, sharp, strong, durable, premium, "
        "easy to ...), safety or health point, or a special ability that is true for some products of this kind and false for others, "
        "and the sources do not show it.\n"
        "Test: could another product of this same kind NOT have this? If yes and the sources do not show it, drop. "
        "If every product of this kind has it, or the sources make it obvious, keep.\n"
        "Return JSON: {\"verdicts\": [{\"n\": 1, \"keep\": true, \"why\": \"one short reason\"}]} with one verdict per claim.")

    def validate(t):
        try:
            v = json.loads(t).get("verdicts")
            if not isinstance(v, list) or len(v) < len(items):
                return "need one verdict for each of the %d claims" % len(items)
            for x in v:
                if not isinstance(x, dict) or "keep" not in x:
                    return "every verdict needs keep"
        except Exception:
            return "not valid JSON"
        return None
    v = gen(prompt, validate)["verdicts"]
    out = []
    for n in range(len(items)):
        x = next((y for y in v if isinstance(y, dict) and str(y.get("n")) == str(n + 1)), None) or v[n]
        k = x.get("keep")
        out.append(((k.strip().lower() in ("true", "yes")) if isinstance(k, str) else bool(k), str(x.get("why", "")).strip()))
    return out


def _ai_evidence(parts, texts, gen):
    listing = "\n".join("%d) %s" % (i + 1, t) for i, t in enumerate(texts))
    src = "\n".join("[%s]: %s" % (k, v) for k, v in parts.items())
    prompt = (
        "You are a strict fact checker for product video scripts. You may use ONLY the sources below as evidence.\n"
        + src + "\n\nTEXTS TO CHECK:\n" + listing + "\n\n"
        "For EACH text list every claim it makes about the product: what the product is, accessories or items included, how many, "
        "colour, print or design, material, size, a feature or ability. Ignore storytelling and everyday wording (didi, college, gift, "
        "questions used as a hook).\n"
        "For EACH claim give \"quote\": the exact words copied from ONE of the sources above that prove it. Copy, do not paraphrase. "
        "If no source says it, give an empty quote. A related but different fact does not prove a claim (for example 'charging case' does "
        "not prove 'Type-C'). Do not add anything from your own knowledge in this step.\n"
        "Return JSON: {\"results\": [{\"n\": 1, \"claims\": [{\"claim\": \"short claim in English\", \"quote\": \"exact words from the sources or empty\"}]}]} "
        "with one entry per text.")

    def validate(t):
        try:
            items = json.loads(t).get("results")
            if not isinstance(items, list) or len(items) < len(texts):
                return "need one result for each of the %d texts" % len(texts)
            for it in items:
                if not isinstance(it, dict) or not isinstance(it.get("claims"), list):
                    return "every result needs a claims list"
                for c in it["claims"]:
                    if not isinstance(c, dict) or "claim" not in c or "quote" not in c:
                        return "every claim needs claim and quote"
        except Exception:
            return "not valid JSON"
        return None
    return gen(prompt, validate)["results"]


def _default_gen(prompt, validate):
    import llm
    return json.loads(llm.generate([prompt], validate=validate))


def check_texts(p, texts, gen=None):
    """Fact check several texts. gen(prompt, validate) -> parsed JSON dict (script_writer passes its own AI caller).
    Stage 1: code checks. Stage 2: the AI must quote proof. Stage 3: for every claim that has no proof, a common-sense review
    decides keep or drop (a claim with a number / spec word / colour / material that the sources lack is never rescued)."""
    parts, raw, src_n = sources(p)
    out = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        if not (t or "").strip():
            out[i] = {"ok": True, "unsupported": [], "ai": True}
            continue
        bad = local_issues(t, raw, src_n)
        if bad:
            out[i] = {"ok": False, "unsupported": bad, "ai": False}
        else:
            todo.append(i)
    if not todo:
        return out
    res = None
    try:
        res = _ai_evidence(parts, [texts[i] for i in todo], gen or _default_gen)
    except Exception as e:
        print("  [factcheck] AI check could not run (%s): only the code checks were applied" % str(e)[:90])
    pending = {}
    for k, i in enumerate(todo):
        if res is None:
            out[i] = {"ok": True, "unsupported": [], "ai": False}
            continue
        it = next((x for x in res if isinstance(x, dict) and str(x.get("n")) == str(k + 1)), None) or (res[k] if k < len(res) else {})
        claims = [c for c in (it.get("claims") or []) if isinstance(c, dict)]
        if not claims:         # the AI listed nothing: it did not really check
            out[i] = {"ok": True, "unsupported": [], "ai": False}
            continue
        bad = []
        for c in claims:
            claim, quote = str(c.get("claim", "")).strip(), str(c.get("quote", "")).strip()
            if not quote:
                bad.append((claim, "no source says this"))
            elif not _quote_found(quote, src_n):
                bad.append((claim, "the quoted words are not in the sources"))
            elif local_issues(claim, quote, _n(quote)) or not _related(claim, quote):
                bad.append((claim, "the quoted words do not support it"))
        pending[i] = bad
    # Stage 3: common sense, one AI call for all unproven claims. Hard facts (numbers, specs, colours, materials) are never rescued.
    appeal = [(i, claim, why) for i, bad in pending.items() for claim, why in bad if not local_issues(claim, raw, src_n)]
    verdict = {}
    if appeal:
        try:
            for (i, claim, _w), (keep, why) in zip(appeal, _common_sense_review(parts, appeal, gen or _default_gen)):
                verdict[(i, claim)] = (keep, why)
        except Exception as e:
            print("  [factcheck] common-sense review could not run (%s): unproven claims are dropped" % str(e)[:90])
    for i, bad in pending.items():
        dropped, kept = [], []
        for claim, why in bad:
            keep, cs = verdict.get((i, claim), (False, ""))
            if keep:
                kept.append(claim)
                print("  [factcheck] kept by common sense: %s (%s)" % (claim, cs or "plain common sense"))
            else:
                dropped.append(claim + " (" + why + (("; common sense: " + cs) if cs else "") + ")")
        out[i] = {"ok": not dropped, "unsupported": dropped, "ai": True, "common_sense": kept}
    return out


def explain(res):
    return "; ".join(res["unsupported"]) if res["unsupported"] else "ok"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if a != "--no-ai"]
    if len(args) < 2:
        print('Use: python factcheck.py "text to check" "product name" ["details"] ["page text"] ["page link"] [--no-ai]')
        sys.exit(0)
    prod = {"name": args[1], "description": args[2] if len(args) > 2 else "", "page_text": args[3] if len(args) > 3 else "",
            "url": args[4] if len(args) > 4 else ""}
    if "--no-ai" in sys.argv:
        _, raw_, n_ = sources(prod)
        found = local_issues(args[0], raw_, n_)
        print("CODE CHECK:", ("NOT OK: " + "; ".join(found)) if found else "nothing wrong found")
    else:
        r_ = check_texts(prod, [args[0]])[0]
        print(("OK" if r_["ok"] else "NOT OK") + (" (AI checked)" if r_["ai"] else " (code checks only)") + ":", explain(r_))
