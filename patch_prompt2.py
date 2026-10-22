import re
src = open("script_writer.py", encoding="utf-8-sig").read()

OLD_FACTS = r"""    feats = [str(x) for x in r.get("features", [])][:2]
    facts = _clean(str(r.get("plain_name", "")) + ": " + "; ".join(feats) + ". Show: " + str(r.get("surprise_detail", "")))
    if _banned(facts):
        facts = _clean(str(r.get("plain_name", "")))
    if len(facts) > 130:
        facts = facts[:130].rsplit(" ", 1)[0]
"""
NEW_FACTS = r"""    feats = [_clean(str(x)) for x in r.get("features", []) if str(x).strip()]
    facts = "; ".join(feats[:2])
    if len(facts) > 95:
        facts = feats[0] if feats and len(feats[0]) <= 95 else ""
    if facts and _banned(facts):
        facts = ""
"""
NEW_FUNC = r"""def _build_prompt(name, body, scenes, extra="", facts=""):
    # Text typed into YouTube Create. Spoken lines and the call to action are always kept in full.
    # Leftover room goes first to the shot that DEMONSTRATES the product, then to the product facts.
    # Nothing is cut in the middle: a part either fits completely or is left out.
    tail = " The voiceover must END with these exact words: " + CTA_HI
    parts = [s.strip() for s in (scenes or "").split(";") if s.strip()]
    demo = parts[-1].rstrip(" .") if parts else ""
    look = "Vertical 9:16, 10 seconds, bright clean background, no text on screen, no people."
    base = _clean(" ".join(x for x in [STYLE, (extra or "").strip(), "Product: " + name + ".", look] if x))
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
    return _clean(base + " " + mid + " " + voice + tail)"""

if src.count(OLD_FACTS) != 1:
    raise SystemExit("facts wala hissa nahi mila, kuch nahi badla")
out = src.replace(OLD_FACTS, NEW_FACTS, 1)
out, n = re.subn(r"def _build_prompt\(.*?(?=\n\n\ndef make_script)", lambda m: NEW_FUNC, out, count=1, flags=re.S)
if n != 1:
    raise SystemExit("_build_prompt nahi mila, kuch nahi badla")
open("script_writer.py.bak6", "w", encoding="utf-8").write(src)
open("script_writer.py", "w", encoding="utf-8").write(out)
print("script_writer.py patched")
