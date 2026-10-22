def rd(p):
    return open(p, encoding="utf-8-sig").read()

SW = [
(r'''}
PEOPLE_FORCE = os.getenv("PEOPLE_MODE", "auto").strip().lower()''',
 r'''    "pet": {
        "scene": "the product from the reference image being played with or used by ONE friendly, healthy adult pet of the kind the "
                 "product is made for (a cat for cat toys, a dog for dog toys), natural and cute, no people in the frame, "
                 "plain bright background, no text on screen.",
        "look": "9:16, bright clean background, no text on screen, a healthy adult pet plays with or uses the product, no people.",
        "ref": "Use the reference photo only for the product's look. Do not copy any person or animal from it: show a new cute adult pet.",
    },
}
PEOPLE_FORCE = os.getenv("PEOPLE_MODE", "auto").strip().lower()'''),
(r'''def _people_mode(p, r):''',
 r'''PETWORDS = re.compile(r"\b(cat|cats|kitten|kittens|dog|dogs|puppy|puppies|pet|pets)\b[\w\s-]{0,25}\b(toy|toys|bed|bowl|collar|leash|"
                      r"feeder|house|scratcher|tunnel|ball|brush|harness|treat|treats|food|litter)\b|"
                      r"\b(toy|toys|ball|bed|bowl|collar|leash)\b[\w\s-]{0,10}\bfor\s+(cats?|dogs?|pets?|puppy|puppies|kittens?)\b", re.I)


def _animal(name):
    t = (name or "").lower()
    if re.search(r"\b(cat|cats|kitten|kittens)\b", t):
        return "cat"
    if re.search(r"\b(dog|dogs|puppy|puppies)\b", t):
        return "dog"
    return "cat or dog (the kind this product is made for)"


def _people_mode(p, r):'''),
(r'''    if KIDS.search(text) and mode != "none":''',
 r'''    if mode != "pet" and PETWORDS.search(p["name"]):          # cat/dog products: show the pet, not hands
        mode, why = "pet", "pet product (the name says cat/dog)"
    if KIDS.search(text) and mode != "none":'''),
(r'''none or hands or adult_model\", \"people_reason\"''',
 r'''none or hands or adult_model or pet\", \"people_reason\"'''),
(r'''people = who should appear in the video. adult_model:''',
 r'''people = who should appear in the video. pet: the product is made for a pet (cat or dog toys, beds, bowls, collars, leashes, grooming): a healthy adult pet shows it in use, always choose pet for pet products instead of hands. adult_model:'''),
(r'''    look = rule["look"] + " " + rule["ref"]
''',
 r'''    look = rule["look"] + " " + rule["ref"]
    if people == "pet":
        look = re.sub(r"\bpet\b", _animal(name), look)
'''),
]
RS = [
(r'''    _f("quiet_closeup", "quiet cinematic", "silent",''',
 r'''    _f("pet_play", "cute pet play", "music", "pet_play", 3.0, "pet",
       audio="Voice: NO voice, no talking. Music: playful cheerful instrumental, soft happy pet sounds.",
       core="Vertical 10-second cute pet video: a healthy adult pet plays with the product in warm daylight.",
       plan="a cute pet video: a healthy adult pet (the kind the product is made for) plays with or uses the product exactly the way it is meant to be used; only real, possible actions, with the product's real look.",
       shots="The pet curiously sniffs the {p}; it bats or uses it playfully; a happy close-up of the pet with the {p}."),
    _f("quiet_closeup", "quiet cinematic", "silent",'''),
(r'''def _fmt_people(f, product_mode, kids):
    if f["people"] == "none":''',
 r'''def _fmt_people(f, product_mode, kids):
    if f["people"] == "pet":
        return "pet"
    if f["people"] == "none":'''),
(r'''    forced = os.getenv("REEL_FORMAT", "").strip().lower()
''',
 r'''    if product_mode == "pet":       # pet products: the pet must be in the video (no hands-only or product-only formats)
        cands = [f for f in cands if f["people"] in ("product", "pet")]
    else:
        cands = [f for f in cands if f["people"] != "pet"]
    forced = os.getenv("REEL_FORMAT", "").strip().lower()
'''),
]
jobs = [("script_writer.py", SW), ("reel_styles.py", RS)]
data = {}
for path, pairs in jobs:
    src = rd(path)
    for o, n in pairs:
        if src.count(o) != 1:
            raise SystemExit(path + ": patch ki jagah nahi mili (" + o[:45].strip().replace("\n", " ") + "), kuch nahi badla")
    data[path] = src
for path, pairs in jobs:
    out = data[path]
    for o, n in pairs:
        out = out.replace(o, n, 1)
    open(path + ".bak10", "w", encoding="utf-8").write(data[path])
    open(path, "w", encoding="utf-8").write(out)
    print(path, "patched")
