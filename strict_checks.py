"""Strict, CODE-based video checks that run AFTER verify_video.py. They replace the "AI, does it look fine?" judgement.

1) Pronunciation: the transcript of what the voice REALLY said is compared word by word with the text the voice was
   given (script["body_hi"] + script["cta_hi"]). Words that are changed ("chupchap" -> "chapchap") are listed and fail the video.
2) Product match: done by product_match.py. ALL reference pictures and 8 frames from the WHOLE video go into ONE picture
   and the AI decides if the video shows the same product. It understands combos (earbuds + watch): the video is right when
   every main item appears in at least one frame. (The old check used one reference photo and 3 frames, and a combo video
   whose frames showed one item at a time failed.)

apply(report, video_path, ref_path, p, script) updates the report from verify_video.py (pass / reason / checks).
Try the pronunciation check alone:  python strict_checks.py "what the voice said" "what it should say"
Optional .env: PRON_MIN_BODY=0.85   VERIFY_PROVIDER_ORDER=gemini,groq
"""
import io, json, os, re, subprocess, sys, tempfile

PRON_MIN_BODY = float(os.getenv("PRON_MIN_BODY", "0.85"))   # share of script words that must be said correctly
PRON_MIN_CTA = float(os.getenv("PRON_MIN_CTA", "0.7"))      # call to action is judged more leniently (English words)

# ------------------------------------------------- Devanagari -> sound letters ----
_V = {"\u0905": "a", "\u0906": "aa", "\u0907": "i", "\u0908": "ii", "\u0909": "u", "\u090a": "uu", "\u090f": "e",
      "\u0910": "ai", "\u0913": "o", "\u0914": "au", "\u0911": "o", "\u090d": "e", "\u090b": "ri"}
_M = {"\u093e": "aa", "\u093f": "i", "\u0940": "ii", "\u0941": "u", "\u0942": "uu", "\u0947": "e", "\u0948": "ai",
      "\u094b": "o", "\u094c": "au", "\u0949": "o", "\u0945": "e", "\u0943": "ri"}
_C = {"\u0915": "k", "\u0916": "kh", "\u0917": "g", "\u0918": "gh", "\u0919": "n", "\u091a": "ch", "\u091b": "chh",
      "\u091c": "j", "\u091d": "jh", "\u091e": "n", "\u091f": "t", "\u0920": "th", "\u0921": "d", "\u0922": "dh",
      "\u0923": "n", "\u0924": "t", "\u0925": "th", "\u0926": "d", "\u0927": "dh", "\u0928": "n", "\u092a": "p",
      "\u092b": "ph", "\u092c": "b", "\u092d": "bh", "\u092e": "m", "\u092f": "y", "\u0930": "r", "\u0932": "l",
      "\u0935": "v", "\u0936": "sh", "\u0937": "sh", "\u0938": "s", "\u0939": "h",
      "\u0958": "k", "\u0959": "kh", "\u095a": "g", "\u095b": "j", "\u095c": "d", "\u095d": "dh", "\u095e": "f", "\u095f": "y"}
_DEV = "\u0900-\u0963\u0966-\u097f"
_TOKEN = re.compile("[" + _DEV + "]+|[A-Za-z']+")


def tokens(text):
    return _TOKEN.findall(text or "")


def is_dev(tok):
    return bool(tok) and "\u0900" <= tok[0] <= "\u097f"


def _translit(w):
    out, i, n, inherent = [], 0, len(w), False
    while i < n:
        ch = w[i]
        inherent = False
        if ch in _V:
            out.append(_V[ch])
            i += 1
        elif ch in _C:
            out.append(_C[ch])
            i += 1
            if i < n and w[i] == "\u093c":
                i += 1
            if i < n and w[i] in _M:
                out.append(_M[w[i]])
                i += 1
            elif i < n and w[i] == "\u094d":
                i += 1
            else:
                out.append("a")
                inherent = True
        elif ch in "\u0902\u0901":
            out.append("n")
            i += 1
        elif ch == "\u0903":
            out.append("h")
            i += 1
        else:
            i += 1
    if inherent and out:
        out.pop()                       # a word does not end in the inherent 'a'
    return "".join(out)


_REPL = [("chh", "C"), ("ch", "C"), ("sh", "s"), ("ph", "f"), ("kh", "k"), ("gh", "g"), ("bh", "b"), ("dh", "d"),
         ("th", "t"), ("jh", "j"), ("ck", "k"), ("q", "k"), ("x", "ks"), ("w", "v"), ("z", "j"), ("ee", "i"),
         ("oo", "u"), ("aa", "a"), ("ii", "i"), ("uu", "u"), ("ai", "e"), ("ei", "e"), ("ey", "e"), ("au", "o"),
         ("ou", "o"), ("c", "k"), ("C", "c")]


def sound(tok):
    """Spelling-independent 'sound string' of a word (Devanagari or Latin)."""
    s = _translit(tok) if is_dev(tok) else tok
    s = re.sub(r"[^a-z]", "", s.lower())
    for a, b in _REPL:
        s = s.replace(a, b)
    s = re.sub(r"(?<=[aeiou])y(?=[aeiou])", "", s)      # jaayega ~ jaaega
    s = re.sub(r"(.)\1+", r"\1", s)
    s = re.sub(r"([aeiou])n$", r"\1", s)
    return s


def _skeleton(s):
    return re.sub(r"[aeiou]", "", s)


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ratio(a, b):
    m = max(len(a), len(b))
    return 1.0 if m == 0 else 1 - _lev(a, b) / m


def pair_sim(e, h):
    """Similarity (0..1) of an expected word and a heard word."""
    a, b = sound(e), sound(h)
    if is_dev(e) != is_dev(h):                     # one Latin, one Devanagari (English word): compare consonants only
        return _ratio(_skeleton(a), _skeleton(b)) if min(len(a), len(b)) >= 4 else _ratio(a, b)
    return _ratio(a, b)


def _thr(e, h, lenient):
    L = max(len(sound(e)), len(sound(h)))
    t = 1.0 if L <= 2 else 0.8 if L <= 4 else 0.9 if L <= 9 else 0.8
    if is_dev(e) != is_dev(h):
        t = 0.75
    return min(t, 0.65) if lenient else t


def _merge(E, H):
    """Spaces are unreliable in transcripts: join two neighbouring words when together they match one word."""
    def merge_into(A, B):
        out, i = [], 0
        while i < len(A):
            if i + 1 < len(A):
                joined = A[i] + A[i + 1]
                best_j = max((pair_sim(joined, b) for b in B), default=0)
                single = max((max(pair_sim(A[i], b), pair_sim(A[i + 1], b)) for b in B), default=0)
                if best_j >= 0.85 and best_j > single:
                    out.append(joined)
                    i += 2
                    continue
            out.append(A[i])
            i += 1
        return out
    return merge_into(E, H), merge_into(H, E)


def align(E, H):
    """Match each expected word with the heard word at the same place (global alignment)."""
    n, m, GAP = len(E), len(H), -0.4
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        S[i][0] = i * GAP
    for j in range(1, m + 1):
        S[0][j] = j * GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            S[i][j] = max(S[i - 1][j] + GAP, S[i][j - 1] + GAP, S[i - 1][j - 1] + 2 * pair_sim(E[i - 1], H[j - 1]) - 1)
    pairs, i, j = [None] * n, n, m
    while i > 0 and j > 0:
        if abs(S[i][j] - (S[i - 1][j - 1] + 2 * pair_sim(E[i - 1], H[j - 1]) - 1)) < 1e-9:
            pairs[i - 1] = H[j - 1]
            i, j = i - 1, j - 1
        elif abs(S[i][j] - (S[i - 1][j] + GAP)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return pairs


def check_pronunciation(heard, expected_body, expected_cta=""):
    """Compare what the voice said with the text it was given."""
    eb, ec, H = tokens(expected_body), tokens(expected_cta), tokens(heard)
    E = eb + ec
    E2, H2 = _merge(E, H)
    nb = len(eb)
    # merging may change the count: keep the CTA boundary by re-merging each part on its own
    Eb, _ = _merge(eb, H)
    Ec, _ = _merge(ec, H) if ec else ([], [])
    E2 = Eb + Ec
    nb = len(Eb)
    pairs = align(E2, H2)
    body_ok = body_n = cta_ok = cta_n = 0
    wrong = []
    for idx, (e, h) in enumerate(zip(E2, pairs)):
        is_cta = idx >= nb
        good = h is not None and pair_sim(e, h) >= _thr(e, h, lenient=is_cta)
        if is_cta:
            cta_n += 1
            cta_ok += good
        else:
            body_n += 1
            body_ok += good
        if not good and not is_cta:
            wrong.append((e, h))
    body_score = body_ok / body_n if body_n else 1.0
    cta_score = cta_ok / cta_n if cta_n else 1.0
    english_wrong = [(e, h) for e, h in wrong if not is_dev(e) and len(sound(e)) >= 3]   # an ENGLISH word (pink, zip, sponge) said wrongly
    return {"ok": body_score >= PRON_MIN_BODY, "cta_ok": cta_score >= PRON_MIN_CTA,
            "body_score": round(body_score, 2), "cta_score": round(cta_score, 2),
            "wrong": wrong, "english_wrong": english_wrong, "heard_words": len(H)}


def explain(res):
    parts = []
    for e, h in res["wrong"][:6]:
        parts.append("'%s' (%s) -> %s" % (e, sound(e), ("'%s' (%s)" % (h, sound(h))) if h else "not said"))
    return "; ".join(parts)


# ------------------------------------------------------------- product match ----
def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def frames_from_video(path, n=3):
    """n JPEG frames (bytes) spread over the video."""
    dur = 0.0
    try:
        dur = float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path]).stdout.strip())
    except Exception:
        pass
    if dur <= 0:
        dur = 8.0
    frames = []
    tmp = tempfile.mkdtemp()
    for k in range(n):
        t = dur * (k + 1) / (n + 1)
        out = os.path.join(tmp, "f%d.jpg" % k)
        _run(["ffmpeg", "-y", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1", "-q:v", "3", out])
        if os.path.exists(out) and os.path.getsize(out) > 0:
            frames.append(open(out, "rb").read())
    return frames


def composite(ref_path, frames, height=560):
    """ONE picture: reference photo on the left, video frames on the right (every AI provider accepts one image)."""
    from PIL import Image, ImageDraw
    panels = []
    ref = Image.open(ref_path).convert("RGB")
    panels.append(("REFERENCE PHOTO", ref))
    for i, fb in enumerate(frames):
        panels.append(("VIDEO FRAME %d" % (i + 1), Image.open(io.BytesIO(fb)).convert("RGB")))
    resized = []
    for label, im in panels:
        w = max(1, int(im.width * height / im.height))
        resized.append((label, im.resize((w, height))))
    W = sum(im.width for _, im in resized) + 10 * (len(resized) + 1)
    canvas = Image.new("RGB", (W, height + 40), (30, 30, 30))
    d, x = ImageDraw.Draw(canvas), 10
    for label, im in resized:
        canvas.paste(im, (x, 34))
        d.text((x + 4, 10), label, fill=(255, 255, 255))
        x += im.width + 10
    if canvas.width > 1400:
        canvas = canvas.resize((1400, int(canvas.height * 1400 / canvas.width)))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _gen(contents, validate):
    import llm
    order = os.getenv("VERIFY_PROVIDER_ORDER", "").strip()
    old = getattr(llm, "ORDER", None)
    if order and old is not None:
        llm.ORDER = [p.strip().lower() for p in order.split(",") if p.strip()]
    try:
        return json.loads(llm.generate(contents, validate=validate))
    finally:
        if order and old is not None:
            llm.ORDER = old


def product_match(ref_path, video_path, product, ref_paths=None, loose=False):
    """Returns (True/False/None, reason). None = could not check.
    Looks at the WHOLE video against ALL reference pictures (ref_paths, else just ref_path); see product_match.py."""
    refs = [p for p in (ref_paths or [ref_path]) if p and os.path.exists(p)]
    if not refs:
        return None, "no reference photo to compare"
    order = os.getenv("VERIFY_PROVIDER_ORDER", "").strip()
    llm_mod, old = None, None
    try:
        import llm as llm_mod
        old = getattr(llm_mod, "ORDER", None)
        if order and old is not None:
            llm_mod.ORDER = [x.strip().lower() for x in order.split(",") if x.strip()]
        import product_match as pm
        return pm.check(video_path, refs, product, loose=loose)
    except Exception as e:
        return None, "product comparison could not run: " + str(e)[:100]
    finally:
        if order and llm_mod is not None and old is not None:
            llm_mod.ORDER = old


# ------------------------------------------------------------------- apply ----
STRICT_ENGLISH = os.getenv("STRICT_ENGLISH", "1") != "0"     # one wrongly said ENGLISH word (pink, zip, sponge ...) fails the video by itself


def apply(report, video_path, ref_path, p, script, ref_paths=None):
    """Tighten the verdict of verify_video.py with the two strict checks.
    The format of the reel (script["style"]) decides which checks make sense:
      voice_check 'strict'   -> every spoken word is compared with the script (talk / narrator formats)
      voice_check 'presence' -> a sung line must exist, the words are NOT compared (jingle)
      voice_check 'none'     -> music / ASMR / silent format: no speech check at all
      loose_product          -> animation: the 'same product' judge is told that a cartoon / clay look is expected"""
    checks = report.setdefault("checks", {})
    theirs = dict(checks)
    problems, notes = [], []
    style = script.get("style") or {}
    vc = script.get("voice_check") or style.get("voice_check") or "strict"
    loose = bool(style.get("loose_product"))

    heard = (report.get("transcript") or "").strip()
    if script.get("body_hi"):
        exp_body, exp_cta = script["body_hi"], script.get("cta_hi", "")
    else:
        exp_body, exp_cta = script.get("body", ""), script.get("cta", "")
    pron = None
    if vc != "strict":
        checks["pronunciation"] = None
        print("  [strict] voice words not compared with a script (%s format, voice check: %s)" % (style.get("label") or "?", vc))
        if vc == "presence":
            notes.append("sung line: only checked that a voice exists, listen to the words yourself")
    elif len(tokens(heard)) >= 4 and exp_body:
        res = check_pronunciation(heard, exp_body, exp_cta)
        pron = res["ok"]
        checks["pronunciation"] = res["ok"]
        checks["cta_words"] = res["cta_ok"]
        print("  [strict] voice: %d%% of script words said correctly (need %d%%), call to action %d%%"
              % (res["body_score"] * 100, PRON_MIN_BODY * 100, res["cta_score"] * 100))
        if res["wrong"]:
            print("  [strict] changed words:", explain(res))
        if not res["ok"]:
            problems.append("the voice changed or mispronounced words: " + explain(res))
        if STRICT_ENGLISH and res["ok"] and res.get("english_wrong"):
            checks["english_words"] = False
            problems.append("an English word was said wrongly: " + "; ".join(
                "'%s' -> %s" % (e, ("'%s'" % h) if h else "not said") for e, h in res["english_wrong"][:4]))
        if not res["cta_ok"]:
            problems.append("the call to action (link / comment / DM) was not said clearly")
    else:
        checks["pronunciation"] = None
        notes.append("could not transcribe the voice: listen to it yourself")

    same, why = product_match(ref_path, video_path, p, ref_paths, loose=loose)
    checks["same_product"] = same
    print("  [strict] product match:", {True: "SAME", False: "DIFFERENT", None: "not checked"}[same], "-", why[:240])
    if same is False:
        problems.append("the product in the video does not match the reference photo(s) (" + why[:200] + ")")
    elif same is None:
        notes.append(why)

    ignore = set()
    if pron is not None or vc != "strict":
        ignore |= {"cta_spoken", "matches_script", "voice_is_hinglish"}
    if vc == "none":
        ignore.add("has_speech")
    if not script.get("spoken_cta", True):
        ignore.add("cta_spoken")
    failed = [k for k, v in theirs.items() if v is False and k not in ignore]
    report["pass"] = not failed and not problems
    if report["pass"]:
        ran = []
        if pron:
            ran.append("voice words match the script")
        elif vc != "strict":
            ran.append("no word check needed (%s format)" % (style.get("label") or vc))
        if same:
            ran.append("product matches the photo" + (" (animation: loose match)" if loose else ""))
        report["reason"] = ("; ".join(ran) or "NOT VERIFIED by the strict checks") + ((" | NOTE: " + "; ".join(notes)) if notes else "")
    else:
        report["reason"] = "; ".join(problems + (["failed: " + ", ".join(failed)] if failed else []))
    return report


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 3:
        print('Use: python strict_checks.py "what the voice said" "what it should say" ["call to action it should say"]')
        sys.exit(0)
    r = check_pronunciation(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    print(r)
    print("changed words:", explain(r) or "none")
