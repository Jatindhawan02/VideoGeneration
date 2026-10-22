"""reel_styles.py - decides HOW each reel is made, so the videos do not all feel the same. No AI, no quota.

pick(p, r, product_mode, kids) -> style dict. It chooses, every time at random (but looking at the last videos):
  1. VOICE  : what you hear      talk / narrator / jingle (sung) / music only / ASMR (product sounds) / silent
  2. JOB    : what the video does  comedy, confession, reveal, product speaks, aesthetic show-off, claymation, ASMR, how-to, loop, gift reveal ...
  3. AD     : how much it feels like an ad   pure (no selling) / light (one soft mention) / direct (as before)
  + a shuffled backdrop (setting), camera move and mood, an optional trend idea from trends.md, and a caption CTA wording.
record(style) remembers the choice (style_history.json, last 10) so the next video is different.

Try it without any AI:   python reel_styles.py 12        (prints 12 picks in a row)
Force one format (test): REEL_FORMAT=claymation python main_link.py "LINK"     (ids: see FORMATS below)
Optional .env: AD_MIX=40,40,20   (pure, light, direct)    STYLE_HISTORY=10
trends.md (same folder, optional): one trend idea per line; 0-2 of them are offered to the AI as optional spice.
"""
import json, os, random, re, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE, "style_history.json")
TRENDS_FILE = os.path.join(BASE, "trends.md")
KEEP = int(os.getenv("STYLE_HISTORY", "10"))


def _flag(name, default=None):
    """Reads a true/false setting from the environment (the control page sets CTA and TEXT_OVERLAY). Unset = default."""
    v = os.getenv(name, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# VIDEO_TYPE (from the control page): which formats are allowed for each choice
#   hands = only adult hands handle the product | person = one adult model | pet = formats built around a pet (people="pet" only) | animated = cartoon / clay / talking product
VIDEO_TYPES = {
    "hands": lambda f: f["people"] in ("hands", "product", "human"),
    "person": lambda f: f["people"] in ("product", "human"),
    "pet": lambda f: f["people"] == "pet",      # only formats built around a pet (the generic "product" talk formats do not fit)
    "animated": lambda f: bool(f["loose"]),
}

try:
    _mix = [float(x) for x in os.getenv("AD_MIX", "40,40,20").split(",")]
    AD_WEIGHTS = {"pure": _mix[0], "light": _mix[1], "direct": _mix[2]}
except Exception:
    AD_WEIGHTS = {"pure": 40.0, "light": 40.0, "direct": 20.0}

# ------------------------------------------------------------------ dials ----
# voice -> (voice_check, spoken call to action allowed)
#   voice_check: strict = every word is compared with the script; presence = a voice/song must exist but words are NOT checked;
#                none = no voice expected (music / ASMR / silent): speech checks are skipped
VOICE_PROPS = {"talk": ("strict", True), "narrator": ("strict", True), "jingle": ("presence", False),
               "music": ("none", False), "asmr": ("none", False), "silent": ("none", False)}

AD_BRIEF = {
    "pure": "AD LEVEL = pure content: NO selling, no pitch, no praise, no 'buy'. It must feel like entertainment a person would "
            "share. The product just appears naturally in the scene; naming it is optional (this overrides any rule about naming it).",
    "light": "AD LEVEL = light: no pitch and no praise. The product is named or shown once, in passing, like a friend mentioning it.",
    "direct": "AD LEVEL = direct: clearly say what the product is and the one thing that makes it handy.",
}
AD_CAPTION = {
    "pure": "The caption must read like normal entertaining content (a relatable or funny line, a tiny story), NOT like an ad: "
            "do not list features or praise the product; mention it at most once in passing. ",
    "light": "Keep the caption casual: one friendly line plus one short mention of what the product is, no pitch. ",
    "direct": "Keep the caption clear: say what the product is and why it is handy, in 2 to 3 short lines. ",
}

# every wording keeps the words "Comment LINK" (the Creator Club auto-DM listens for that comment)
CAPTION_CTAS = [
    "Comment LINK to get the link in your DM",
    "Comment LINK and I will DM you the link",
    "Want it? Comment LINK, the link lands in your DM",
    "Comment LINK, link seedha aapke DM mein",
    "Comment LINK and check your DM for the link",
]

SETTINGS = ["soft pastel gradient backdrop", "sunlit window sill with soft shadows", "warm wooden desk",
            "white marble surface in soft daylight", "colourful paper backdrop", "cosy bed with pastel sheets",
            "kitchen counter in morning light", "dark moody backdrop with one warm lamp glow", "green balcony with plants",
            "fairy-light bokeh backdrop", "terrazzo tabletop", "beige linen cloth surface"]
CAMERAS = ["slow macro push-in", "top-down flat lay", "slow orbit around the product", "handheld POV",
           "low-angle hero shot", "quick whip-pan reveal", "steady close-up", "gentle slide from left to right"]
MOODS = ["cosy", "playful", "dreamy", "energetic", "calm and minimal", "funny", "warm and festive", "fresh and bright"]

HOOK_DEFS = {
    "relatable_pain": "an annoyance everyone knows, said honestly or funnily",
    "pov": "starts with POV or Jab/Agar, puts the viewer in a moment",
    "curiosity_gap": "a question or tease that line 2 answers",
    "surprise_detail": "line 2 reveals the one unexpected real feature",
    "mini_story": "a tiny scene with someone from home (Mummy, Bhai, Didi, Dost)",
    "funny_exaggeration": "a silly over-the-top reaction to an everyday moment; the product quietly appears as the twist",
    "mummy_reaction": "a Mummy, Bhai or Didi reaction line, then a twist",
    "confession": "a small honest confession a friend would whisper, then the reveal",
    "gift_moment": "someone gives or receives this as a gift, a tiny warm moment",
    "narrator_tease": "a calm storyteller line that sets a scene, then reveals the product",
    "product_speaks": "the PRODUCT itself talks in first person to its owner, cute and a little cheeky",
}


def _f(id, label, voice, job, weight, people, **kw):
    d = {"id": id, "label": label, "voice": voice, "job": job, "weight": weight, "people": people,
         "dialogue": False, "hook_types": [], "answer_rule": True, "loose": False, "needs_facts": False,
         "use_base_style": False, "brief": "", "core": "", "audio": "", "voice_line": "", "scene_rule": "",
         "plan": "", "shots": ""}
    d.update(kw)
    return d


_PRON = " Pronounce every word clearly and exactly as written, do not skip, merge or change any word."

# people: "product" = follow the people rule chosen for the product, "none" = product only, "hands" = adult hands only
FORMATS = [
    # ---- voice formats with spoken lines (two lines, about 13-16 words) ----
    _f("classic", "problem and answer", "talk", "story", 2.0, "product", dialogue=True, use_base_style=True,
       hook_types=["relatable_pain", "pov", "curiosity_gap", "surprise_detail", "mini_story"], answer_rule=True,
       brief="a relatable small problem in line 1, the product as the answer in line 2 (the classic hook and payoff)."),
    _f("comedy_skit", "comedy skit", "talk", "comedy", 2.0, "product", dialogue=True, use_base_style=True,
       hook_types=["funny_exaggeration", "mummy_reaction", "mini_story", "confession", "pov"], answer_rule=False,
       brief="a tiny comedy skit. Line 1 = a funny desi setup, line 2 = the punchline in which the product quietly saves the day "
             "or is the twist. Line 2 does NOT have to be a solution to line 1: a joke, a twist or a reaction is better."),
    _f("voice_note", "friend confession", "talk", "story", 1.5, "product", dialogue=True, use_base_style=True,
       hook_types=["confession", "pov", "mini_story", "curiosity_gap", "funny_exaggeration"], answer_rule=False,
       brief="a whispered voice-note confession to a friend. Line 1 = a small honest confession or tease, line 2 = the twist that "
             "reveals the product. It must sound like gossip between friends, never like an ad."),
    _f("narrator_reveal", "storyteller reveal", "narrator", "reveal", 1.5, "product", dialogue=True, use_base_style=True,
       hook_types=["narrator_tease", "curiosity_gap", "surprise_detail", "gift_moment", "mini_story"], answer_rule=False,
       voice_line="Voice: calm, warm storyteller narrator, speaking everyday Hindi." + _PRON,
       brief="a calm storyteller narrator. Line 1 sets a tiny scene or builds curiosity, line 2 reveals the product as the "
             "surprise. Slightly dramatic, storybook tone, still plain everyday words."),
    _f("product_speaks", "product talks", "talk", "animation", 1.2, "none", dialogue=True, loose=True, answer_rule=False,
       hook_types=["product_speaks", "funny_exaggeration", "confession"],
       voice_line="Voice: cute, playful character voice of the product itself, speaking everyday Hindi." + _PRON,
       scene_rule="the product itself as a cute animated character with tiny eyes and a mouth, SAME shape and colours as in the "
                  "reference image, bouncing and talking to the camera, no people, no text on screen.",
       core="Vertical 10-second cute 3D animation: the product itself is a little character with tiny eyes and a mouth, "
            "same shape and colours as the reference photo, bouncing and talking to camera.",
       brief="the PRODUCT is the speaker and talks in first person (main, mujhe, mera) to its owner, cute and a little cheeky. "
             "Line 1 = what it says on meeting the viewer, line 2 = a funny or sweet follow-up that shows what it is."),
    # ---- formats without dialogue ----
    _f("jingle", "sung jingle", "jingle", "animation", 1.0, "none", loose=True,
       voice_line="Voice: a cheerful, catchy SUNG line in Hindi with music. Sing every word clearly and exactly as written.",
       core="Vertical 10-second cute animated music clip: the product bounces and sparkles in time with the song.",
       plan="a cute animated music clip: the product bounces and sparkles to ONE short catchy Hindi sung line about it (the lyric).",
       shots="The {p} bounces and sparkles in time with the beat; it spins once at the end."),
    _f("lofi_showoff", "aesthetic show-off", "music", "aesthetic", 1.2, "none",
       audio="Voice: NO voice, no talking. Music: cheerful lo-fi instrumental.",
       core="Vertical 10-second aesthetic show-off of the product only, smooth camera move, soft sparkle.",
       plan="an aesthetic show-off of the product alone with smooth camera moves; the viewer just enjoys looking at it.",
       shots="Slow macro move across the {p}; a soft sparkle; end on a clean full view."),
    _f("claymation", "claymation animation", "music", "animation", 1.2, "none", loose=True,
       audio="Voice: NO voice, no talking. Music: playful xylophone.",
       core="Vertical 10-second claymation stop-motion animation: the product comes alive, wiggling and flipping on the beat.",
       plan="a claymation stop-motion animation where the product comes alive on the beat (wiggle, flip, bounce) with its real shape and colours.",
       shots="The {p} wiggles to life; it flips on the beat; it lands with a tiny bounce."),
    _f("asmr_hands", "ASMR close-up", "asmr", "satisfying", 1.2, "hands",
       audio="Voice: NO voice, NO music. Sound: only soft close-up product sounds (taps, clicks, fabric).",
       core="Vertical 10-second satisfying ASMR close-up: an adult's hands slowly handle the product, golden window light.",
       plan="satisfying ASMR close-ups: an adult's hands slowly handle the product; big tactile moments (tap, flip, press, open) that are really possible with this product.",
       shots="Close-up of hands slowly turning the {p}; fingertip taps; a slow flip to show the other side."),
    _f("howto_steps", "simple how-to", "music", "howto", 1.2, "hands", needs_facts=True,
       audio="Voice: NO voice, no talking. Music: simple upbeat instrumental.",
       core="Vertical 10-second simple how-to: only an adult's hands, three clear steps with the product.",
       plan="a simple 3-step how-to with an adult's hands. Each step must be something the VERIFIED FACTS or page details clearly "
            "support. If little is known, use plain steps: hold it, turn it, show the other side.",
       shots="Step 1 hold the {p}; step 2 turn it over; step 3 show the other side."),
    _f("satisfying_loop", "satisfying loop", "music", "loop", 1.0, "none",
       audio="Voice: NO voice, no talking. Music: soft rhythmic beat.",
       core="Vertical 10-second satisfying seamless loop of one smooth repeating motion of the product.",
       plan="ONE smooth repeating motion of the product that ends exactly where it started, so it loops satisfyingly.",
       shots="One smooth repeating motion of the {p}, ending exactly where it began."),
    _f("gift_reveal", "gift reveal", "music", "gift_reveal", 1.0, "hands",
       audio="Voice: NO voice, no talking. Music: warm happy instrumental.",
       core="Vertical 10-second gift reveal: an adult's hands open a plain paper wrap to reveal the product.",
       plan="a gift reveal: adult hands unwrap a plain wrapping to reveal the product, then a happy final shot of it.",
       shots="Hands slide a plain paper wrap off; the {p} is revealed; a small happy tilt toward the camera."),
    _f("pet_play", "cute pet play", "music", "pet_play", 3.0, "pet",
       audio="Voice: NO voice, no talking. Music: playful cheerful instrumental, soft happy pet sounds.",
       core="Vertical 10-second cute pet video: a healthy adult pet plays with the product in warm daylight.",
       plan="a cute pet video: a healthy adult pet (the kind the product is made for) plays with or uses the product exactly the way it is meant to be used; only real, possible actions, with the product's real look.",
       shots="The pet curiously sniffs the {p}; it bats or uses it playfully; a happy close-up of the pet with the {p}."),
    _f("quiet_closeup", "quiet cinematic", "silent", "aesthetic", 0.8, "product",
       audio="Voice: NO voice, no talking, no music. Sound: soft room ambience only.",
       core="Vertical 10-second quiet cinematic close-up, slow and calm.",
       plan="a quiet cinematic close-up that lets the product speak for itself, slow and calm.",
       shots="Slow calm close-up of the {p}; the light shifts gently; end on the full view."),
]
FORMATS += [
    _f("lifestyle_vibe", "lifestyle vibe", "music", "lifestyle", 1.4, "product",
       audio="Voice: NO voice, no talking. Music: trendy chill lo-fi beat.",
       core="Vertical 10-second trendy lifestyle vibe: the product used in a cool everyday moment, smooth camera, aesthetic look.",
       plan="a trendy lifestyle vibe: the product is used in a cool everyday moment (desk, cafe table, room corner) exactly the way it is "
            "meant to be used, as described in WHAT THE CAMERA SHOWS; only real, possible actions.",
       shots="The {p} in a cool everyday moment; a smooth close-up while it is used; end on a relaxed full view."),
    _f("unboxing_smile", "happy unboxing", "music", "gift_reveal", 1.2, "human",
       audio="Voice: NO voice, no talking. Music: happy upbeat instrumental.",
       core="Vertical 10-second happy unboxing: a parcel is opened and the product is revealed with a small smile moment.",
       plan="a happy unboxing: a plain parcel is opened, the product is revealed and shown off with a small happy reaction.",
       shots="A plain parcel is opened; the {p} is lifted out; a small happy tilt toward the camera."),
    _f("cartoon_3d", "cute 3D cartoon", "music", "animation", 1.2, "none", loose=True,
       audio="Voice: NO voice, no talking. Music: cheerful playful tune.",
       core="Vertical 10-second cute 3D cartoon scene: the product is the hero of a tiny colourful cartoon world, bouncing and sparkling.",
       plan="a cute 3D cartoon scene where the product, with its real shape and colours, is the hero of a tiny colourful world.",
       shots="The {p} pops into a colourful cartoon world; it bounces and sparkles; it spins once at the end."),
]
# ---- pet formats (used by the "Pet" video type): a healthy adult pet is always in the video, never a person in the frame ----
FORMATS += [
    _f("pet_storyteller", "pet storyteller", "narrator", "reveal", 1.5, "pet", dialogue=True, use_base_style=True,
       hook_types=["narrator_tease", "curiosity_gap", "surprise_detail", "mini_story", "funny_exaggeration"], answer_rule=False,
       voice_line="Voice: calm, warm storyteller narrator, speaking everyday Hindi." + _PRON,
       brief="a calm storyteller narrator talks about the PET in the video. Line 1 sets a tiny scene about the pet or builds curiosity, "
             "line 2 reveals the product as the pet's new favourite thing. Warm storybook tone, plain everyday words. "
             "Only the narrator speaks, the pet does not talk."),
    _f("pet_inner_voice", "pet's inner voice", "talk", "comedy", 1.5, "pet", dialogue=True, use_base_style=True,
       hook_types=["funny_exaggeration", "confession", "pov", "mini_story"], answer_rule=False,
       voice_line="Voice: cute, playful inner voice of the pet, speaking everyday Hindi, as a voiceover (the pet's mouth does not need to move)." + _PRON,
       brief="the PET's funny inner thoughts as a voiceover in first person (main, mera, mujhe). Line 1 = what the pet is thinking about "
             "the product, line 2 = a cheeky or sweet follow-up that shows what the product is. Playful, never like an ad."),
    _f("pet_owner_voice", "owner's voice-over", "talk", "story", 1.3, "pet", dialogue=True, use_base_style=True,
       hook_types=["relatable_pain", "funny_exaggeration", "mini_story", "pov", "confession"], answer_rule=False,
       voice_line="Voice: warm, casual voice of the pet's owner speaking off-camera in everyday Hindi, no person visible." + _PRON,
       brief="the pet's owner, heard off-camera (nobody is seen), talks about the pet. Line 1 = a relatable or funny pet-parent moment, "
             "line 2 = the twist that shows the pet loving this product. Sounds like a pet parent talking, never like an ad."),
    _f("pet_asmr", "pet ASMR close-up", "asmr", "satisfying", 1.2, "pet",
       audio="Voice: NO voice, NO music. Sound: only soft close-up sounds of the pet using the product (soft pats, gentle crunch, tiny purrs or happy breaths).",
       core="Vertical 10-second satisfying ASMR close-up: a healthy adult pet slowly and happily uses the product, soft daylight.",
       plan="satisfying ASMR close-ups of a healthy adult pet using the product exactly the way it is meant to be used; only real, possible actions.",
       shots="Close-up of the pet's paws or nose meeting the {p}; a gentle, slow use of the {p}; a calm happy close-up of the pet."),
    _f("pet_loop", "cute pet loop", "music", "loop", 1.0, "pet",
       audio="Voice: NO voice, no talking. Music: soft rhythmic cute beat.",
       core="Vertical 10-second satisfying seamless loop of a healthy adult pet repeating one cute motion with the product.",
       plan="ONE cute repeating motion of a healthy adult pet with the product (a pat, a nudge, a roll) that ends exactly where it started, so it loops.",
       shots="The pet repeats one cute motion with the {p}, ending exactly where it began."),
    _f("pet_cosy", "cosy pet moment", "music", "lifestyle", 1.2, "pet",
       audio="Voice: NO voice, no talking. Music: cosy warm lo-fi instrumental.",
       core="Vertical 10-second cosy pet moment: a healthy adult pet relaxes at home with the product, warm light, smooth camera.",
       plan="a cosy home moment where a healthy adult pet relaxes with or uses the product exactly the way it is meant to be used; only real, possible actions.",
       shots="The pet settles down next to the {p}; a smooth close-up while it uses the {p}; end on a relaxed, happy full view."),
]
BY_ID = {f["id"]: f for f in FORMATS}


# ------------------------------------------------ kids products, adult angle ----
# Some products are sold for children (toys, school items). The video app refuses videos that show children, so we never show
# one. When the control page asks for hands or a person we show ADULTS using it as a fun desk toy, stress-buster or collectible
# (a college student, a young professional) instead of dropping the people and making only a product shot.
ADULT_SCENE = {
    "hands": "the product from the reference image, handled by a pair of NEW adult hands only (no face, no body, no children), "
             "shown as a fun desk toy, stress-buster or collectible that grown-ups enjoy, plain bright background, no text on screen.",
    "adult_model": "the product from the reference image, used by ONE adult Indian model in their 20s (a cool college student or young "
                   "professional, a NEW person made from scratch, never the person in the reference photo) as a fun desk toy, "
                   "stress-buster or collectible, relaxed and playful, no children anywhere, plain bright background, no text on screen.",
}
ADULT_LOOK = {
    "hands": "Shown as a fun desk toy for grown-ups.",
    "adult_model": "A cool college-age adult plays with it casually as a fun desk toy, no children anywhere.",
}
ADULT_BRIEF = (" ADULT ANGLE: this product is usually sold for children, but THIS video is for adults. Present it as a fun desk toy, "
               "stress-buster, nostalgic collectible, room-decor piece or quirky gift for a friend. Never mention or show children, "
               "babies, age, learning or growing up.")


def catalog():
    """Every format with the video types it fits and whether it has a voice (used by the control page dropdown)."""
    out = []
    for f in FORMATS:
        out.append({"id": f["id"], "label": f["label"], "voice": f["voice"],
                    "audible": VOICE_PROPS[f["voice"]][0] != "none",
                    "types": [t for t, fn in VIDEO_TYPES.items() if fn(f)]})
    return out


# ---------------------------------------------------------------- history ----
def _load():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as fh:
            h = json.load(fh)
        return h if isinstance(h, list) else []
    except Exception:
        return []


def record(style):
    """Remember this video's choices (last KEEP videos)."""
    h = _load()
    h.append({k: style.get(k) for k in ("format", "voice", "job", "ad", "setting", "camera", "mood", "cta")}
             | {"ts": time.strftime("%Y-%m-%d %H:%M")})
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(h[-KEEP:], fh, ensure_ascii=False, indent=1)
    except Exception as e:
        print("  [style] could not save the history:", str(e)[:80])


def _fresh(options, key, hist, last_n=6):
    used = [h.get(key) for h in hist[-last_n:]]
    pool = [o for o in options if o not in used] or list(options)
    return random.choice(pool)


def read_trends():
    """0-2 random lines from trends.md (comment lines starting with # are ignored)."""
    try:
        with open(TRENDS_FILE, encoding="utf-8") as fh:
            lines = [l.strip(" -*\t") for l in fh.read().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except Exception:
        return ""
    lines = [l for l in lines if len(l) > 3]
    if not lines:
        return ""
    return " | ".join(random.sample(lines, min(len(lines), random.choice([1, 1, 2]))))[:400]


def _weights(cands, hist):
    recent = [h.get("format") for h in hist]
    last = hist[-1] if hist else {}
    out = []
    for f in cands:
        w = float(f["weight"])
        if f["id"] in recent[-3:]:
            w *= 0.05
        elif f["id"] in recent:
            w *= 0.4
        if last and f["voice"] == last.get("voice"):
            w *= 0.6
        if last and f["job"] == last.get("job"):
            w *= 0.7
        if sum(1 for h in hist if h.get("voice") == f["voice"]) >= 4:
            w *= 0.4
        out.append(max(w, 0.001))
    return out


def _pick_ad(hist):
    ws = dict(AD_WEIGHTS)
    last2 = [h.get("ad") for h in hist[-2:]]
    if len(last2) == 2 and last2[0] == last2[1] and last2[0] in ws:
        ws[last2[0]] *= 0.2
    return random.choices(list(ws), weights=list(ws.values()))[0]


def _fmt_people(f, product_mode, kids):   # kids = True means "do not show people at all"
    if f["people"] == "pet":
        return "pet"
    if f["people"] == "none":
        return "none"
    if f["people"] == "hands":
        return "none" if kids else "hands"
    return product_mode            # "product"


def pick(p, r, product_mode="none", kids=False, hist=None, video_type=None, want_cta=None, adult_angle=False, audio=None, forced_format=None):
    """Choose the style of the next reel. p, r are the product and the research dict (r["features"] = verified features).
    kids + adult_angle = a children's product that is shown with ADULTS only (the people choice is kept).
    audio = "voice" (someone talks / narrates / sings) | "none" (music or sounds only) | "" (any). Default: AUDIO_MODE from the environment."""
    hist = _load() if hist is None else hist
    adult = bool(kids and adult_angle and product_mode in ADULT_SCENE)
    if adult:
        kids = False                      # people stay allowed; they are adults only
    feats = [x for x in (r.get("features") or []) if str(x).strip()]
    cands = [f for f in FORMATS if not (f["needs_facts"] and not feats) and not (f["people"] in ("hands", "human") and kids)]
    if product_mode == "pet":       # pet products: the pet must be in the video (no hands-only or product-only formats)
        cands = [f for f in cands if f["people"] in ("product", "pet")]
    else:
        cands = [f for f in cands if f["people"] != "pet"]
    forced = (forced_format if forced_format is not None else os.getenv("REEL_FORMAT", "")).strip().lower()
    if forced == "auto":
        forced = ""
    am = (audio if audio is not None else os.getenv("AUDIO_MODE", "")).strip().lower()
    vt = (video_type if video_type is not None else os.getenv("VIDEO_TYPE", "")).strip().lower()
    if want_cta is None:
        want_cta = _flag("CTA")                 # True = the voice must say the call to action, False = it must not, None = decided at random as before
    if vt in VIDEO_TYPES:
        typed = [x for x in cands if VIDEO_TYPES[vt](x)]
        if typed:
            cands = typed
        else:
            print("  [style] no format fits video type '%s' for this product, choosing freely" % vt)
    if am in ("voice", "none"):
        pool = [x for x in cands if (VOICE_PROPS[x["voice"]][0] != "none") == (am == "voice")]
        if pool:
            cands = pool
        else:
            print("  [style] no format fits video type '%s' with audio '%s', ignoring the audio choice" % (vt or "auto", am))
        if am == "none":
            want_cta = False              # a call to action must be spoken: no voice means no spoken CTA
    if want_cta is True:
        talk = [x for x in cands if VOICE_PROPS[x["voice"]][1]]
        if talk:
            cands = talk
        else:
            print("  [style] no talking format fits this video type, the spoken call to action may be missing")
    f = BY_ID.get(forced) or random.choices(cands, weights=_weights(cands, hist))[0]
    if forced and f["id"] != forced:
        print("  [style] REEL_FORMAT=%s is not a format id, picking at random" % forced)
    ad = _pick_ad(hist)
    if want_cta is True and ad == "pure":       # a pure-content video cannot also sell: when the CTA is required use a light or direct ad level
        ad = random.choice(["light", "direct"])
    check, cta_ok = VOICE_PROPS[f["voice"]]
    if want_cta is False:
        spoken = False
    elif want_cta is True:
        spoken = bool(cta_ok)
    else:
        spoken = bool(cta_ok and ad != "pure")
    setting, camera, mood = _fresh(SETTINGS, "setting", hist), _fresh(CAMERAS, "camera", hist), _fresh(MOODS, "mood", hist)
    used_cta = [h.get("cta") for h in hist[-3:]]
    cta = random.choice([c for c in CAPTION_CTAS if c not in used_cta] or CAPTION_CTAS)
    look = "Camera: %s, %s mood." % (camera, mood)
    people = _fmt_people(f, product_mode, kids)
    scene_rule, brief, plan = f["scene_rule"], f["brief"], f["plan"]
    adult = adult and people in ADULT_SCENE
    if adult:
        scene_rule = scene_rule or ADULT_SCENE[people]
        brief = (brief + ADULT_BRIEF).strip() if brief else ""
        plan = (plan + ADULT_BRIEF).strip() if plan else ""
    return {
        "format": f["id"], "label": f["label"], "voice": f["voice"], "job": f["job"], "ad": ad,
        "dialogue": f["dialogue"], "hook_types": list(f["hook_types"]), "answer_rule": f["answer_rule"],
        "voice_check": check, "spoken_cta": spoken, "video_type": vt or "auto", "want_cta": want_cta,
        "loose_product": f["loose"], "needs_facts": f["needs_facts"], "use_base_style": f["use_base_style"],
        "people": people, "adult_angle": adult,
        "brief": brief, "plan": plan, "shots": f["shots"], "scene_rule": scene_rule,
        "voice_line": f["voice_line"], "audio_line": f["audio"],
        "yt_line": (f["core"] + " " + look).strip(), "look_extra": look,
        "ad_brief": AD_BRIEF[ad], "caption_tone": AD_CAPTION[ad], "caption_cta": cta,
        "setting": setting, "camera": camera, "mood": mood, "trend": read_trends(),
    }


def as_music_only(style):
    """The jingle could not be written safely: make the same video with music only."""
    s = dict(style)
    s.update({"voice": "music", "voice_check": "none", "spoken_cta": False,
              "audio_line": "Voice: NO voice, no talking. Music: cheerful playful instrumental."})
    return s


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "--json":      # used by server.js for the style dropdown
        print(json.dumps(catalog()))
        sys.exit(0)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    h = _load()
    for i in range(n):
        s = pick({"name": "demo"}, {"features": ["demo feature"]}, "hands", False, hist=h)
        print("%2d) %-16s voice=%-8s job=%-12s ad=%-6s spoken_cta=%-5s check=%-8s | %s | %s | %s"
              % (i + 1, s["format"], s["voice"], s["job"], s["ad"], s["spoken_cta"], s["voice_check"],
                 s["setting"], s["camera"], s["mood"]))
        h.append({k: s.get(k) for k in ("format", "voice", "job", "ad", "setting", "camera", "mood", "cta")})
        h = h[-KEEP:]
