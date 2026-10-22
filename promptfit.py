"""Keep the YouTube Create prompt under its character limit WITHOUT losing the spoken lines or the call to action.

fit(prompt, limit=None) -> prompt that is at most `limit` characters (default YT_PROMPT_MAX, 880; the app allows 900).

The back part of the prompt is never touched: everything from 'Voice:' to the end (the spoken lines and the exact
call to action). The front part is shortened by dropping its least important sentences first:
  1. 'Facts: ...'            2. the generic style opener ('Create a 10 second ...')
  3. 'IMPORTANT ...' (retry note; shortened first, then dropped)
  4. style sentences about the reference photo / 'Animate only the product'
  5. 'Shot: ...'              last: product name, look and reference-photo rules (kept as long as possible)
"""
import os, re

_MARKERS = ("Voice:", "Voiceover:", "The voiceover must END")


def _rank(s):
    low = s.lower().strip()
    if low.startswith("facts:"):
        return 0
    if low.startswith("create a") or "10 second" in low or "natural voiceover" in low:
        return 1
    if low.startswith("important") or low.startswith("fix this"):
        return 2
    if ("reference" in low and "still image" in low) or "animate only" in low:
        return 3
    if low.startswith("shot:"):
        return 4
    return 9


def _shorten(s, n=110):
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "."


def _join(front, back):
    return (" ".join(x for x in (front.strip(), back.strip()) if x)).strip()


def fit(prompt, limit=None):
    limit = int(limit or os.getenv("YT_PROMPT_MAX", "880"))
    t = re.sub(r"\s+", " ", prompt or "").strip()
    if len(t) <= limit:
        return t
    idx = [t.find(m) for m in _MARKERS if t.find(m) > 0]
    cut = min(idx) if idx else -1
    front, back = (t[:cut].strip(), t[cut:].strip()) if cut > 0 else (t, "")
    sents = [s for s in re.split(r"(?<=[.!?])\s+", front) if s.strip()]

    def total():
        return len(_join(" ".join(sents), back))

    while total() > limit:
        ranked = [(_rank(s), i) for i, s in enumerate(sents) if _rank(s) < 9]
        if not ranked:
            break
        r, i = min(ranked, key=lambda x: (x[0], -x[1]))      # lowest importance first, later sentence first
        if r == 2 and len(sents[i]) > 120:
            sents[i] = _shorten(sents[i], 110)               # keep a short version of the retry note first
            if total() <= limit:
                break
            continue
        sents.pop(i)
    out = _join(" ".join(sents), back)
    if len(out) > limit:                                      # still too long: shorten the front, never the back
        if back:
            room = limit - len(back) - 1
            front = " ".join(sents)
            front = front[:max(0, room)].rsplit(" ", 1)[0] if room > 0 else ""
            out = _join(front, back)
        else:
            out = out[:limit].rsplit(" ", 1)[0]
    return out
