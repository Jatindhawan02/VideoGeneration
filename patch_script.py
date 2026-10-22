src = open("script_writer.py", encoding="utf-8-sig").read()
PAIRS = [
(r"""ONLY the product from the reference image, simple motion, plain bright """,
 r"""ONLY the product from the reference image, ONE clear motion that DEMONSTRATES its real feature or surprise detail from the research (how it works, what it does), plain bright """),
(r"""Also write what the camera shows (only the product, no people, no text on screen).""",
 r"""Also write what the camera shows (only the product, no people, no text on screen). The motion must show how the product works or its surprise detail."""),
(r"""def _build_prompt(name, body, scenes, extra=""):""",
 r"""def _build_prompt(name, body, scenes, extra="", facts=""):"""),
(r"""+ name + ".", look] if x))""",
 r"""+ name + ".", (("Facts: " + facts.strip()) if facts else ""), look] if x))"""),
(r"""    print("  [script] meaning:", chosen.get("english") or "(not given)")
    return {""",
 r"""    print("  [script] meaning:", chosen.get("english") or "(not given)")
    feats = [str(x) for x in r.get("features", [])][:2]
    facts = _clean(str(r.get("plain_name", "")) + ": " + "; ".join(feats) + ". Show: " + str(r.get("surprise_detail", "")))
    if _banned(facts):
        facts = _clean(str(r.get("plain_name", "")))
    if len(facts) > 130:
        facts = facts[:130].rsplit(" ", 1)[0]
    print("  [script] facts for YouTube:", facts)
    return {"""),
(r"""        "yt_prompt": _build_prompt(p["name"], body_hi, scenes),""",
 r"""        "facts": facts,
        "yt_prompt": _build_prompt(p["name"], body_hi, scenes, facts=facts),"""),
(r"""    return _build_prompt(p["name"], script.get("body_hi") or script["body"], script["scenes"], extra)""",
 r"""    return _build_prompt(p["name"], script.get("body_hi") or script["body"], script["scenes"], extra, facts=script.get("facts", ""))"""),
]
for o, n in PAIRS:
    if src.count(o) != 1:
        raise SystemExit("Patch ki jagah nahi mili (" + o[:40].strip() + "), kuch nahi badla")
out = src
for o, n in PAIRS:
    out = out.replace(o, n, 1)
open("script_writer.py.bak", "w", encoding="utf-8").write(src)
open("script_writer.py", "w", encoding="utf-8").write(out)
print("script_writer.py patched")
