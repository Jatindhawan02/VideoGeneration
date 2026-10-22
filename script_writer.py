"""Product research -> 5 candidate scripts -> senior-editor pick + polish -> prompt for YouTube Create.

make_script(p) returns a dict (same keys as before, main_link.py needs no change):
  body       the two spoken lines WITHOUT the call-to-action
  voiceover  body + the spoken call-to-action (always the last thing said)
  scenes     short visual notes
  yt_prompt  the prompt text to type into YouTube Create (the CTA is always kept at the end)
  research   what the AI worked out about the product

Preview scripts without the phone:
  python script_writer.py "Product name" "short details from the page"

Your own taste: write rules / favourite lines in script_style.md (same folder). They are given to the AI first.
Optional .env: SCRIPT_PROVIDER_ORDER=gemini,groq  -> which AI provider writes the scripts (default: same order as everything else).
"""
import json, os, re
import llm
import promptfit
from config import SHORT_PROMPT_STYLE

# What the voice must say at the end of every reel (change with SPOKEN_CTA in .env)
CTA = os.getenv("SPOKEN_CTA", "Link chahiye? Comment LINK, DM mein mil jayega.").strip()
# The same call to action as the VOICE reads it: Hindi words in Devanagari, English words (Link, Comment, DM) in Latin letters
CTA_HI = os.getenv("SPOKEN_CTA_HI", "Link चाहिए? Comment LINK, DM में मिल जाएगा।").strip()
# YouTube Create prompt box has a limit; keep the prompt under this many characters
MAX_PROMPT = int(os.getenv("YT_PROMPT_MAX", "880"))      # the app allows 900; keep a safety margin

STYLE = (SHORT_PROMPT_STYLE or "").strip().strip('"').strip("'")
if STYLE.upper().startswith("SHORT_PROMPT_STYLE="):
    STYLE = STYLE.split("=", 1)[1].strip()

BASE = os.path.dirname(os.path.abspath(__file__))
STYLE_FILE = os.path.join(BASE, "script_style.md")

# Generic ad-speak and risky claims: a script containing any of these is rejected by the code (not by the AI).
BANNED = ["game changer", "gamechanger", "must have", "must-have", "musthave", "life changing", "life-changing",
          "amazing", "awesome", "perfect", "best", "guaranteed", "number 1", "no 1", "no. 1", "hurry", "limited",
          "offer", "sale", "discount", "price", "sasta", "cheap", "bestseller", "trending now", "wont believe",
          "won't believe", "quality", "premium", "value for money",
          "bachha", "bachcha", "baccha", "bachche", "bacche", "baby", "kid", "kids", "child", "children"]
# Common spoken Hindi words: a Hinglish line must contain at least two (so it is not plain English)
HINDI = set("""hai hain ho hota hoti hote tha thi ka ki ke ko se mein toh bas ab abhi yeh ye wo woh tum tumhare aap aapke
mera meri tera teri apna apni apne yaar arre dekho dekh kar karo kara raha rahi rahe wala wali wale sab kuch koi ek sirf
bhi hi kabhi har jaise lag laga lagta lagti nikal nikla nikal andar bahar upar neeche liye pata na nahi kya kyun kaise
kab jab aur ya pe par isme isko uska uski unka jaata jaati aata aati rehta rehti gila gira""".split())


# ------------------------------------------------------------ people in the video ----
# Decided PER PRODUCT (not a fixed "no people"): clothes/jewellery/beauty need an adult wearing them, tools and gadgets
# need hands, and products that look best alone (or are for children) stay people-free.
# Force one mode for every video with PEOPLE_MODE=none|hands|adult_model in .env (default: auto).
PEOPLE = {
    "none": {
        "scene": "ONLY the product from the reference image, plain bright background, no people, no text on screen.",
        "look": "9:16, bright clean background, no text on screen, no people.",
        "ref": "If the reference photo shows a person, do not show that person, only the product.",
    },
    "hands": {
        "scene": "the product from the reference image, being held or used by a pair of NEW adult hands only (not the person in the "
                 "reference photo; no face, no body, no children), plain bright background, no text on screen.",
        "look": "9:16, bright clean background, no text on screen, only an adult's hands touch the product, no face.",
        "ref": "If the reference photo shows a person, do not copy them: no face or body from the photo, only new adult hands.",
    },
    "adult_model": {
        "scene": "the product from the reference image, being worn or used by ONE adult Indian model (young adult, friendly, natural) "
                 "who is a NEW model made from scratch, never the person in the reference photo, "
                 "medium or close-up shot, plain bright background, no children, no text on screen.",
        "look": "9:16, bright clean background, no text on screen, one adult model wearing or using the product, no children.",
        "ref": "Use the reference photo only for the product's design, colour and fit. Do NOT copy the person in it: "
               "create a completely new adult model with a different face, hair and body. Never show the original model.",
    },
}
PEOPLE_FORCE = os.getenv("PEOPLE_MODE", "auto").strip().lower()
KIDS = re.compile(r"\b(baby|babies|kid|kids|child|children|infant|toddler|newborn|bachha|bachcha|baccha|bachche|bacche|school\s*bag)\b", re.I)
WEARABLE = re.compile(r"\b(kurti|kurta|kurtis|saree|sari|dress|top|tops|tshirt|t-shirt|shirt|jeans|jacket|hoodie|sweatshirt|lehenga|"
                      r"salwar|suit|dupatta|shorts|leggings|palazzo|skirt|gown|blouse|co-ord|coord|shrug|jumpsuit|earring|earrings|"
                      r"necklace|jewellery|jewelry|bracelet|bangle|bangles|anklet|pendant|watch|sunglasses|shoes|sandals|heels|"
                      r"slippers|sneakers|scarf|stole|lipstick|kajal|eyeliner|makeup|nail\s*polish|hair\s*clip|scrunchie)\b", re.I)


def _people_mode(p, r):
    """Returns (mode, reason). mode is 'none', 'hands' or 'adult_model'."""
    text = (p["name"] + " " + (p.get("description") or ""))
    if PEOPLE_FORCE in PEOPLE:
        return PEOPLE_FORCE, "forced by PEOPLE_MODE in .env"
    mode = str(r.get("people", "")).strip().lower().replace(" ", "_").replace("-", "_")
    why = str(r.get("people_reason", "")).strip()
    if mode not in PEOPLE:                                  # the AI gave no usable answer: simple word check
        mode, why = ("adult_model", "wearable product") if WEARABLE.search(p["name"]) else ("none", "no clear need for a person")
    if KIDS.search(text) and mode != "none":                # the video app refuses child content: never show a person
        mode, why = "none", "child/baby product: no people (the video app refuses child content)"
    return mode, why


# ------------------------------------------------------------------ helpers ----
def _clean(t):
    """Plain text that is safe to type (no quotes, %, backslashes, newlines, price, curly punctuation)."""
    t = (t or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u2014", "-").replace("\u2013", "-")
    t = re.sub(r"[\u20b9]\s?\d[\d,]*|Rs\.?\s?\d[\d,]*", "", t)
    t = t.replace('"', "").replace("\u201c", "").replace("\u201d", "").replace("%", " percent")
    t = t.replace("\\", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def _style_notes():
    """The creator's own taste (script_style.md). Empty if the file does not exist."""
    try:
        with open(STYLE_FILE, encoding="utf-8") as f:
            return f.read().strip()[:1500]
    except Exception:
        return ""


def _gen_json(prompt, validate_fn):
    """One AI call that must return a JSON object. Optionally uses a different provider order for scripts."""
    order = os.getenv("SCRIPT_PROVIDER_ORDER", "").strip()
    old = getattr(llm, "ORDER", None)
    if order and old is not None:
        llm.ORDER = [p.strip().lower() for p in order.split(",") if p.strip()]
    try:
        return json.loads(llm.generate([prompt], validate=validate_fn))
    finally:
        if order and old is not None:
            llm.ORDER = old


def _words(t):
    return re.findall(r"[A-Za-z0-9']+", t or "")


def _banned(text):
    low = (text or "").lower()
    for phrase in BANNED:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low):
            return phrase
    return None


def hard_issues(l1, l2, h1="", h2="", eng=()):
    """Rules the CODE enforces on a script. Returns a list of problems (empty list = OK).
    eng = English words of this product (name / features) that must stay in Latin letters in h1/h2."""
    text = (l1 + " " + l2).strip()
    issues = []
    if any(ord(ch) > 127 for ch in text):
        issues.append("use plain English letters only (Hinglish), no Hindi script or emoji")
    n, n1 = len(_words(text)), len(_words(l1))
    if n < 11 or n > 18:
        issues.append("%d words (need 11 to 18)" % n)
    if n1 < 3 or n1 > 10:
        issues.append("hook must be 3 to 10 words")
    if sum(1 for w in _words(text.lower()) if w in HINDI) < 2:
        issues.append("not Hinglish enough")
    b = _banned(text)
    if b:
        issues.append("banned word: " + b)
    if h1 or h2:
        hi = h1 + " " + h2
        toks = re.findall(r"[\u0900-\u0963\u0966-\u097F]+|[A-Za-z']+", hi)
        dev = [t for t in toks if "\u0900" <= t[0] <= "\u097F"]
        if not toks or len(dev) / len(toks) < 0.5:
            issues.append("mixed-script lines: Hindi words must be in Devanagari")
        if len(toks) < 8 or len(toks) > 20:
            issues.append("mixed-script lines have %d words (need 8 to 20)" % len(toks))
        latin = set(t.lower() for t in toks if not ("\u0900" <= t[0] <= "\u097F"))
        for w in sorted(set(x.lower() for x in _words(text)) & set(eng)):
            if w not in latin:
                issues.append("keep the English word '%s' in Latin letters in the Devanagari lines" % w)
    return issues


def _score(c, key_tokens):
    """Soft score used to order candidates and as the fallback if the editor step fails."""
    l1, l2 = c["line1"], c["line2"]
    text = (l1 + " " + l2).lower()
    s = 0
    if "?" in l1 or l1.lower().startswith(("pov", "jab ", "agar ")):
        s += 2
    if 4 <= len(_words(l1)) <= 8:
        s += 1
    if 13 <= len(_words(l1 + " " + l2)) <= 16:
        s += 1
    s += min(3, len(key_tokens & set(re.findall(r"[a-z]{4,}", text))))
    return s


def _end_hi(t):
    t = _clean(t)
    return t if t[-1:] in ".?!\u0964" else (t + "\u0964")


def _end(t):
    t = _clean(t)
    return t if t[-1:] in ".?!" else (t + ".")


def _visible_line(p):
    """Two trusted sources, each for its own kind of fact."""
    out = ""
    v = (p.get("visible") or "").strip()
    if v:
        out += ("WHAT THE PRODUCT PHOTO SHOWS (trusted for looks: colours, prints, shapes, how many items are visible; "
                "a photo cannot show size, capacity or material): " + v + "\n")
    it = (p.get("image_text") or "").strip()
    if it:
        out += ("TEXT PRINTED ON THE PRODUCT'S OWN PICTURES (trusted for specs, what is in the box, charging type, ranges, hours, sizes): "
                + it + "\n")
    t = (p.get("page_text") or "").strip()
    if t:
        out += ("TEXT READ FROM THE APP SCREENS (trusted for size, capacity, material, quantity and specifications; "
                "may contain noise): " + t[:1200] + "\n")
    if out:
        out += ("A claim is fine if EITHER source supports it. Never claim a print, colour, count, size, capacity, material or feature "
                "that neither source supports, and never contradict either source.\n")
    return out


def claims_check_many(p, r, texts, detail=False):
    """Fact check texts with factcheck.py (code checks + AI that must quote its proof; the AI's own research notes are NOT trusted).
    Returns a list of (ok, [unsupported claims]); with detail=True a list of dicts that also say whether the AI really ran."""
    import factcheck
    res = factcheck.check_texts(p, texts, gen=_gen_json)
    if not detail:
        return [(x["ok"], x["unsupported"]) for x in res]
    return res


def claims_check(p, r, text):
    return claims_check_many(p, r, [text])[0]


# ----------------------------------------------------------------- research ----
def research(p):
    """Step 1: understand the product like a strategist before writing a single line."""
    prompt = (
        "You are a sharp Indian short-video strategist for affiliate products.\n"
        "Product: " + p["name"] + "\nDetails from the product page: " + (p["description"] or "none") + "\n" + _visible_line(p) + "\n"
        "Think about it like a person who actually shops on Meesho, then answer as JSON:\n"
        "{\"plain_name\": \"what a friend would call it in 3-6 simple words\", "
        "\"audience\": \"who in India buys this (age, situation)\", "
        "\"problem\": \"the small everyday annoyance or desire behind it, in a relatable desi situation\", "
        "\"buyer_moment\": \"ONE specific real moment when someone needs or sees this (concrete scene: where, what happens)\", "
        "\"surprise_detail\": \"the single most surprising or charming REAL detail from the page\", "
        "\"features\": [\"2-4 real features from the page or from the text printed on the product pictures, no invented specs\"], "
        "\"best_angle\": \"the single best reel angle for it: relatable pain, curiosity gap, surprise reveal, gift moment or mini story\", "
        "\"people\": \"none or hands or adult_model\", \"people_reason\": \"one short reason\"}\n"
        "people = who should appear in the video. adult_model: the product is worn or applied on the body (clothes, jewellery, "
        "footwear, watch, glasses, hair or beauty items) or sells by how it looks on a person. hands: people hold or operate it "
        "(kitchen, cleaning or organising tools, gadgets, stationery, accessories, toys, items with a mechanism), so only adult hands "
        "show it working. none: it shows everything on its own (decor, lights, display pieces) or it is meant for babies or children.\n"
        "Use ONLY facts from the page. Never mention price, discount, ratings, 'best' or 'guaranteed'."
    )
    keys = ["plain_name", "audience", "problem", "buyer_moment", "surprise_detail", "features", "best_angle", "people"]

    def validate(text):
        try:
            data = json.loads(text)
        except Exception:
            return "not valid JSON"
        if not isinstance(data, dict):
            return "answer must be one JSON object"
        for k in keys:
            if not data.get(k):
                return "missing '" + k + "'"
        return None
    return _gen_json(prompt, validate)


# --------------------------------------------------------------- candidates ----
HOOK_TYPES = ["relatable_pain", "pov", "curiosity_gap", "surprise_detail", "mini_story"]

CRAFT = (
    "You are a senior creative director at a top Indian ad agency who now makes scroll-stopping Instagram Reels for "
    "affiliate products. Your reels get saved and shared because the first 2 seconds give a tiny jolt (recognition, "
    "curiosity or surprise) and the next 4 seconds pay it off with ONE specific, believable benefit.\n\n"
    "FORMAT: a 10 second Reel. The voice speaks 2 lines in about 6 seconds (13 to 16 words in total). A fixed call to "
    "action is added after your lines automatically, so never write one.\n"
    "- LINE 1 = the HOOK: 3 to 9 words, spoken in the first 2 seconds.\n"
    "- LINE 2 = the PAYOFF: says in plain words what the product is AND the one specific thing that makes it neat.\n"
    "- A stranger who has never seen the product must understand what it is and why it is neat. Line 2 must logically "
    "answer or continue line 1.\n\n"
    "CRAFT RULES:\n"
    "- Specific beats generic: every script needs one concrete detail from the product page or one concrete everyday "
    "situation (a real object, place or moment). Never use empty adjectives.\n"
    "- Hinglish = Hindi written in English letters, the way a 22 year old in India talks to a friend in a voice note: "
    "short everyday words (ye, yaar, arre, dekho, na, hai), no textbook Hindi, no translated-sounding English, no "
    "ad-speak. Read each line aloud in your head: if it sounds like an advertisement, rewrite it.\n"
    "- Plain English letters only: no Hindi script, no emojis, no hashtags, no double quotes.\n"
    "- Never write these words: " + ", ".join(sorted(set(b for b in BANNED if " " not in b))) + ".\n"
    "- No price, discount, rating, health or safety claim, no comparison with other brands.\n"
    "- Use ONLY facts from the product page. Never invent a material, size, feature or result.\n\n"
    "- Use only words you hear in daily talk or on Instagram reels. No bookish Hindi, no odd verb forms. Test: say what your two "
    "lines mean in plain English; if that sounds odd, or the two lines do not connect, rewrite them.\n"
    "- Never show or mention a child, baby, kid or bachha (the video app refuses child content). Whether an adult appears in the video "
    "(wearing the product, or only hands) is given in the scene notes rule below.\n"
    "- Also write the SAME two lines as hi1 and hi2 for the video's voice, in MIXED script: every Hindi word in Devanagari, "
    "but every ENGLISH word stays in Latin letters exactly as spelled in English (octopus, smile, pink, zip, sponge, link, "
    "comment, DM, product names). NEVER write an English word in Devanagari. Example: Ye octopus kaisa dikh raha hai? becomes "
    "\u092f\u0947 octopus \u0915\u0948\u0938\u093e \u0926\u093f\u0916 \u0930\u0939\u093e \u0939\u0948? Spell the Hindi words as pronounced in standard Hindi "
    "(\u091a\u0941\u092a\u091a\u093e\u092a, \u0917\u0941\u0938\u094d\u0938\u093e, \u0926\u093f\u0916 \u091c\u093e\u090f\u0917\u093e).\n\n"
    "STYLE EXAMPLES (only to show the level; NEVER reuse these lines or their structure for another product):\n"
    "GOOD (soap dispenser with sponge holder): \"Sink ka sponge hamesha gila rehta hai na? Is dispenser mein uska apna ghar hai.\"\n"
    "GOOD (reversible plush toy): \"Ye strawberry lag raha hai na? Zip kholo, andar se pura khargosh nikal aata hai.\"\n"
    "GOOD (wardrobe organiser): \"Almaari kholte hi sab kuch gir jaata hai? Isme har cheez ka apna dabba hai.\"\n"
    "BAD: \"Yeh product bahut amazing hai, must have for everyone!\" (generic, banned words, no detail)\n"
    "BAD: \"Aaj hum dekhenge ek perfect cheez jo aapki life badal degi.\" (empty promise, you cannot tell what it is)\n"
    "BAD: \"Kitchen ki problem solve karo, quality ke saath style bhi.\" (makes no sense, nothing concrete)\n"
)


def _fact_rule(r):
    """Which product claims are proven, and how line 2 must connect to line 1."""
    f = [str(x) for x in r.get("features", []) if str(x).strip()]
    link = ("LINE 2 MUST ANSWER LINE 1: pick a hook about a small problem that this KIND of product solves, and let line 2 say "
            "what the product is and offer it as the solution (use X instead of Y). Mention a verified feature only as a plain fact "
            "(for example 'it has a handle'), never as an invented reason why it helps. A feature that has nothing to do with the hook "
            "is NOT a payoff: do not write a hook about one thing and a line 2 about another.")
    if f:
        return ("VERIFIED FACTS (the ONLY product claims you may make): " + "; ".join(f) + ". "
                "Anything else in RESEARCH is background for the situation, never a claim about the product. " + link)
    return ("No special product feature is verified. Write only about what the product is and its everyday use "
            "(what the viewer sees it being used for). No claim about size, thickness, material, hanging, strength or special abilities. " + link)


def coherence_many(p, r, cands):
    """AI judge: does line 2 really answer line 1, and does it sound like natural talk? Returns [(ok, why)].
    If the AI is unavailable it lets the scripts through (the fact check has already run)."""
    if not cands:
        return []
    listing = "\n".join("%d. %s | %s   (means: %s)" % (k + 1, c["line1"], c["line2"], c.get("english", ""))
                        for k, c in enumerate(cands))
    facts = "; ".join(str(x) for x in r.get("features", [])) or "none verified"
    prompt = (
        "Product: " + p["name"] + " (a friend would call it: " + str(r.get("plain_name", "")) + ")\n"
        "Verified product facts: " + facts + "\n\n"
        "Each script below is a 2-line Hinglish voiceover for a short product video: line 1 = hook, line 2 = payoff.\n" + listing + "\n\n"
        "Judge each one strictly, like a normal Indian viewer hearing it once. ok = true ONLY if ALL are true:\n"
        "1) line 2 directly answers or continues the question or problem in line 1;\n"
        "2) the benefit follows from what the product is or from its verified facts (no leap of logic);\n"
        "3) it sounds like a natural sentence a person would say.\n"
        "NOT ok example: the hook is about a messy drawer and line 2 only says the product is made of plastic "
        "(the material does not answer the problem).\n"
        "Return JSON: {\"verdicts\": [{\"ok\": true, \"why\": \"short reason if not ok\"}]} with exactly " + str(len(cands)) + " verdicts in the same order.")

    def validate(t):
        try:
            v = json.loads(t).get("verdicts")
            if not isinstance(v, list) or len(v) != len(cands):
                return "need exactly one verdict per script"
            for x in v:
                if not isinstance(x, dict) or "ok" not in x:
                    return "each verdict needs 'ok'"
        except Exception:
            return "not valid JSON"
        return None

    def truthy(v):
        return (v.strip().lower() in ("true", "yes")) if isinstance(v, str) else bool(v)
    try:
        v = _gen_json(prompt, validate)["verdicts"]
        return [(truthy(x.get("ok")), str(x.get("why", ""))) for x in v]
    except Exception as e:
        print("  [script] sense check skipped:", str(e)[:100])
        return [(True, "")] * len(cands)


def candidates(p, r, notes):
    prompt = (
        CRAFT +
        ("\nTHE CREATOR'S OWN NOTES (follow these over everything else):\n" + notes + "\n" if notes else "") +
        "\nPRODUCT: " + p["name"] + "\nDETAILS: " + (p["description"] or "none") + "\n" + _visible_line(p) +
        "RESEARCH: " + json.dumps(r, ensure_ascii=False) + "\n" + _fact_rule(r) + "\n\n"
        "TASK: write 5 different scripts, one for each of these hook types: " + ", ".join(HOOK_TYPES) + ".\n"
        "relatable_pain = an annoyance everyone knows, said honestly or funnily. "
        "pov = starts with POV or Jab/Agar, puts the viewer in a moment. "
        "curiosity_gap = a question or tease that line 2 answers. "
        "surprise_detail = line 2 reveals the one unexpected real feature. "
        "mini_story = a tiny scene with someone from home (Mummy, Bhai, Didi, Dost).\n"
        "Scene notes describe what the camera shows: " + PEOPLE.get(r.get("people"), PEOPLE["none"])["scene"] +
        " ONE clear motion that DEMONSTRATES its real feature or surprise detail from the research (how it works, what it does).\n"
        "Before answering, check each script: 13 to 16 words, hook under 10 words, line 2 names what the product is, "
        "no banned word, sounds like a real person.\n"
        "Return JSON: {\"candidates\": [{\"hook_type\": \"...\", \"line1\": \"...\", \"line2\": \"...\", "
        "\"hi1\": \"line 1: Hindi words in Devanagari, English words in Latin letters\", \"hi2\": \"line 2, same way\", \"english\": \"what the two lines mean in plain English\", "
        "\"scene1\": \"what the camera shows during line 1\", \"scene2\": \"what the camera shows during line 2\"}]}"
    )

    def validate(text):
        try:
            data = json.loads(text)
            items = data.get("candidates")
            if not isinstance(items, list) or len(items) < 3:
                return "need a list of at least 3 candidates"
            for it in items:
                for k in ("line1", "line2", "hi1", "hi2", "scene1", "scene2"):
                    if not str(it.get(k, "")).strip():
                        return "a candidate is missing '" + k + "'"
        except Exception:
            return "not valid JSON"
        return None

    items = _gen_json(prompt, validate)["candidates"]
    out = []
    for it in items:
        out.append({"hook_type": str(it.get("hook_type", "")),
                    "line1": _end(str(it["line1"])), "line2": _end(str(it["line2"])),
                    "hi1": _end_hi(str(it["hi1"])), "hi2": _end_hi(str(it["hi2"])),
                    "english": _clean(str(it.get("english", ""))),
                    "scene1": _clean(str(it["scene1"])), "scene2": _clean(str(it["scene2"]))})
    return out


def edit(p, r, pool, notes):
    """The senior editor: pick the strongest candidate and polish it. Returns a dict or None."""
    listing = "\n".join("%d) [%s] %s %s | Devanagari: %s %s | meaning: %s" % (
        i + 1, c["hook_type"], c["line1"], c["line2"], c["hi1"], c["hi2"], c.get("english", "")) for i, c in enumerate(pool))
    prompt = (
        "You are the ruthless senior editor of a viral Indian Reels team.\n"
        "PRODUCT: " + p["name"] + "\nDETAILS: " + (p["description"] or "none") + "\n" + _visible_line(p) +
        "RESEARCH: " + json.dumps(r, ensure_ascii=False) + "\n" + _fact_rule(r) + "\n"
        + ("THE CREATOR'S OWN NOTES (follow these first): " + notes + "\n" if notes else "") +
        "\nCANDIDATE SCRIPTS (line 1 = hook, line 2 = payoff):\n" + listing + "\n\n"
        "Judge each one on: 1) does the hook stop the scroll in 2 seconds, 2) can a stranger tell what the product is and why it is neat, "
        "3) does line 2 logically follow line 1, 4) does it sound like a real person talking in Hinglish (not an ad, not translated), "
        "5) is there a concrete detail from the product page.\n"
        "Pick the single best one, then POLISH it: fix grammar, replace every generic word with a concrete one, and keep the total at 13 to 16 words. "
        "If all candidates are weak, rewrite the best one completely. Do not add facts that are not in the details. "
        "Never use these words: " + ", ".join(sorted(set(b for b in BANNED if " " not in b))) + ". "
        "Plain English letters only (Latin lines), no emojis, no double quotes, no call to action. No child, baby, kid or bachha. "
        "Check the meaning: if the two lines do not connect or sound odd in plain English, rewrite them. "
        "Also give the final two lines as hi1, hi2 in MIXED script: Hindi words in Devanagari, every English word (octopus, smile, "
        "link, comment, DM, product words) stays in Latin letters, never in Devanagari.\n"
        "Also write what the camera shows (" + PEOPLE.get(r.get("people"), PEOPLE["none"])["scene"] + "). The motion must show how the product works or its surprise detail.\n"
        "Return JSON: {\"pick\": <number of the candidate you started from, or 0 if rewritten>, \"line1\": \"...\", \"line2\": \"...\", "
        "\"hi1\": \"...\", \"hi2\": \"...\", \"english\": \"what the final lines mean in plain English\", "
        "\"scene1\": \"...\", \"scene2\": \"...\", \"why\": \"one short sentence\"}"
    )

    def validate(text):
        try:
            data = json.loads(text)
            for k in ("line1", "line2", "hi1", "hi2", "scene1", "scene2"):
                if not str(data.get(k, "")).strip():
                    return "missing '" + k + "'"
        except Exception:
            return "not valid JSON"
        return None

    try:
        d = _gen_json(prompt, validate)
    except Exception as e:
        print("  [script] editor step skipped:", str(e)[:100])
        return None
    return {"line1": _end(str(d["line1"])), "line2": _end(str(d["line2"])),
            "hi1": _end_hi(str(d["hi1"])), "hi2": _end_hi(str(d["hi2"])),
            "english": _clean(str(d.get("english", ""))),
            "scene1": _clean(str(d["scene1"])), "scene2": _clean(str(d["scene2"])),
            "why": _clean(str(d.get("why", "")))}


# ------------------------------------------------------------------- prompt ----
def _build_prompt(name, body, scenes, extra="", facts="", people="none"):
    # Text typed into YouTube Create. Spoken lines and the call to action are always kept in full.
    # Leftover room goes first to the shot that DEMONSTRATES the product, then to the product facts.
    # Nothing is cut in the middle: a part either fits completely or is left out.
    tail = " The voiceover must END with these exact words: " + CTA_HI
    parts = [s.strip() for s in (scenes or "").split(";") if s.strip()]
    demo = parts[-1].rstrip(" .") if parts else ""
    rule = PEOPLE.get(people, PEOPLE["none"])
    look = rule["look"] + " " + rule["ref"]
    style = STYLE
    if people != "none":        # the general style says "Animate only the product", which would forbid the model / hands
        style = re.sub(r"Animate only the product\.?", "", style, flags=re.I).strip()
    base = _clean(" ".join(x for x in [style, (extra or "").strip(), "Product: " + name + ".", look] if x))
    voice = _clean("Voice: warm, upbeat and natural, like a friend sending a voice note, speaking everyday Hindi. "
                   "Pronounce every word clearly and exactly as written, do not skip, merge or change any word. "
                   "Voiceover: " + body.strip() + ("" if body.strip()[-1:] in ".?!\u0964" else "\u0964"))
    room = MAX_PROMPT - len(base) - len(voice) - len(tail) - 6
    shot = _clean("Shot: " + demo + ".") if demo else ""
    if shot and len(shot) > room:
        shot = ""
    if shot:
        room -= len(shot) + 1
    fact = _clean("Facts: " + facts + ".") if facts else ""
    if fact and len(fact) > room:
        fact = ""
    mid = " ".join(x for x in [fact, shot] if x)
    return promptfit.fit(_clean(base + " " + mid + " " + voice + tail), MAX_PROMPT)   # hard cap, never cuts voice/CTA


def make_script(p, verbose=False):
    notes = _style_notes()
    import image_facts
    image_facts.read(p)          # read the specs/features printed on the product's own pictures first
    r = research(p)
    # Prove the research claims (features + surprise detail) against the page / photos BEFORE any script is written,
    # so the ideas are built on facts and are not thrown away one by one afterwards.
    try:
        _items = [_clean(str(x)) for x in r.get("features", []) if str(x).strip()]
        _sd = _clean(str(r.get("surprise_detail", "")))
        _batch = [f for f in _items if f and not _banned(f)] + ([_sd] if _sd else [])
        if _batch:
            _good = {}
            for _t, _x in zip(_batch, claims_check_many(p, r, _batch, detail=True)):
                _good[_t] = bool(_x["ok"] and _x["ai"])
                if not _good[_t]:
                    print("  [script] research claim dropped before writing (%s): %s"
                          % ("; ".join(_x["unsupported"]) or "AI check could not run", _t))
            r["features"] = [f for f in _items if _good.get(f)]
            if _sd and not _good.get(_sd):
                r["surprise_detail"] = ""
    except Exception as e:
        print("  [script] early fact check skipped:", str(e)[:100])
    mode, why = _people_mode(p, r)
    r["people"] = mode
    print("  [script] people in the video: %s (%s)" % (mode, why))
    key_tokens = set(re.findall(r"[a-z]{4,}", (p["name"] + " " + str(r.get("plain_name", "")) + " "
                                                + " ".join(str(x) for x in r.get("features", []))).lower()))
    eng = set(w for w in re.findall(r"[a-z]{4,}", (p["name"] + " " + str(r.get("plain_name", ""))).lower()) if w not in HINDI)
    cands = candidates(p, r, notes)
    for c in cands:
        c["issues"] = hard_issues(c["line1"], c["line2"], c["hi1"], c["hi2"], eng)
        c["score"] = _score(c, key_tokens)
    good = sorted([c for c in cands if not c["issues"]], key=lambda c: -c["score"])
    pool = good[:4] or sorted(cands, key=lambda c: (len(c["issues"]), -c["score"]))[:3]
    print("  [script] %d ideas written, %d passed the code checks" % (len(cands), len(good)))
    if verbose:
        for c in cands:
            print("   - [%s] %s %s  (score %d)%s" % (c["hook_type"], c["line1"], c["line2"], c["score"],
                                                    "  REJECTED: " + "; ".join(c["issues"]) if c["issues"] else ""))
    final = edit(p, r, pool, notes)
    chosen = pool[0]
    if final:
        bad = hard_issues(final["line1"], final["line2"], final["hi1"], final["hi2"], eng)
        if bad:
            print("  [script] editor result broke a rule (%s) -> using the best candidate instead" % "; ".join(bad))
        else:
            chosen = final
            if verbose and final.get("why"):
                print("  [script] editor:", final["why"])
    # Fact check against the photo and the page: a script must not claim a print, colour or count that is not really there.
    # All candidates are checked in ONE AI call; the first one that passes is used.
    fact_ok_seen = []
    def grounded(cs):
        res = claims_check_many(p, r, [c["line1"] + " " + c["line2"] + " " + c.get("english", "") for c in cs])
        for c, (ok, bad) in zip(cs, res):
            c["ok_facts"], c["unsupported"] = ok, bad
            if not ok:
                print("  [script] rejected (" + c["line1"][:40] + "...): the photo/page does not support: " + "; ".join(bad or ["(unclear)"]))
        okc = [c for c in cs if c["ok_facts"]]
        fact_ok_seen.extend(okc)
        for c, (good_, why_) in zip(okc, coherence_many(p, r, okc)):
            c["coherent"], c["why_incoherent"] = good_, why_
            if not good_:
                print("  [script] rejected (" + c["line1"][:40] + "...): line 2 does not make sense after line 1: " + (why_ or "(no reason)"))
        return next((c for c in okc if c.get("coherent", True)), None)
    tries, seen_lines = [], set()
    for c in [chosen] + good + pool:
        key_ = (c["line1"], c["line2"])
        if key_ not in seen_lines:
            seen_lines.add(key_)
            tries.append(c)
    tries = tries[:8]
    picked = grounded(tries)
    if not picked:
        wrong = sorted({b for c in tries for b in c.get("unsupported", [])})
        print("  [script] no script passed the fact check and the sense check -> writing new ones once")
        extra_note = notes
        if wrong:
            extra_note += "\nNEVER claim: " + "; ".join(wrong) + ". Use ONLY what the photo description and page details say."
        nonsense = sorted({c.get("why_incoherent", "") for c in fact_ok_seen if c.get("why_incoherent")})
        if nonsense:
            extra_note += "\nEarlier ideas were rejected because line 2 did not answer line 1: " + "; ".join(nonsense)[:400] + ". Make line 2 a direct answer to line 1."
        fresh = candidates(p, r, extra_note.strip())
        for c in fresh:
            c["issues"] = hard_issues(c["line1"], c["line2"], c["hi1"], c["hi2"], eng)
            c["score"] = _score(c, key_tokens)
        fresh_good = sorted([c for c in fresh if not c["issues"]], key=lambda c: -c["score"])[:6]
        picked = grounded(fresh_good) if fresh_good else None
    if picked:
        chosen = picked
    elif fact_ok_seen:
        chosen = fact_ok_seen[0]
        print("  [script] WARNING: the facts are fine but the two lines may not flow well, read the script before posting")
    else:
        print("  [script] WARNING: could not fully fact check the script, read it before posting")
    body = _clean(chosen["line1"] + " " + chosen["line2"])
    body_hi = _clean(chosen["hi1"] + " " + chosen["hi2"])
    scenes = _clean("; ".join([chosen["scene1"], chosen["scene2"]]))
    print("  [script] meaning:", chosen.get("english") or "(not given)")
    # Facts for YouTube: each research feature must be proven by the page (code checks + a quoted proof) and the AI must really have run.
    feats = [_clean(str(x)) for x in r.get("features", []) if str(x).strip()]
    feats = [f for f in feats if f and len(f) <= 95 and not _banned(f)]
    facts = ""
    if feats:
        good_f = []
        for f, res_ in zip(feats, claims_check_many(p, r, feats, detail=True)):
            if res_["ok"] and res_["ai"]:
                good_f.append(f)
            else:
                print("  [script] fact dropped (%s): %s" % ("; ".join(res_["unsupported"]) or "AI check could not run", f))
        facts = "; ".join(good_f[:2])
        if len(facts) > 95:
            facts = good_f[0]
    print("  [script] facts for YouTube:", facts)
    return {
        "body": body,
        "voiceover": body + " " + CTA,
        "body_hi": body_hi,                 # what the VOICE reads (Devanagari)
        "cta_hi": CTA_HI,
        "voice_hi": body_hi + " " + CTA_HI,
        "cta": CTA,
        "meaning": chosen.get("english", ""),
        "scenes": scenes,
        "research": r,
        "facts": facts,
        "people": mode,
        "yt_prompt": _build_prompt(p["name"], body_hi, scenes, facts=facts, people=mode),
    }


def retry_prompt(p, script, reason):
    """Prompt for the second attempt: same script, plus what went wrong last time."""
    extra = "IMPORTANT, last video failed because: " + _clean(str(reason))[:160] + ". Fix this. "
    return _build_prompt(p["name"], script.get("body_hi") or script["body"], script["scenes"], extra, facts=script.get("facts", ""),
                         people=script.get("people", "none"))


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print('Use: python script_writer.py "Product name" "short details from the page"')
        sys.exit(0)
    prod = {"name": sys.argv[1], "description": sys.argv[2] if len(sys.argv) > 2 else ""}
    out = make_script(prod, verbose=True)
    print("\nPEOPLE    :", out["people"])
    print("VOICEOVER :", out["voiceover"])
    print("VOICE TEXT (Devanagari, what the video voice reads):", out["voice_hi"])
    print("MEANING   :", out["meaning"])
    print("SCENES    :", out["scenes"])
    print("\nPROMPT FOR YOUTUBE CREATE (%d characters):\n%s" % (len(out["yt_prompt"]), out["yt_prompt"]))
