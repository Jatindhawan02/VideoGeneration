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
               SHOW_PRICE=true|false   (the control page checkbox: may the video / caption mention the price? default false)
               TEXT_LANG=english|hinglish|both   (language of the viral line ideas given to the AI, default both)
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

# ------------------------------------------------------- viral line ideas ----
# (category id, title, [[english, hinglish], ...]).  The control page (index.html, TEXT_LIB) shows the same lines as tap-to-use
# text for the video, so keep the two in sync.  Hinglish lines are written the way a person really talks (correct gender and
# "aap" form), not translated word by word; every line is 40 characters or less (the text-on-video limit).
# Here they are offered to the AI as optional inspiration for the spoken lines / caption.  The "price" lines are used only
# when the price checkbox is on, the "pet" lines only for pet products, the "cta" lines never (the call to action has its own rule).
TEXT_LIBRARY = [
 ('gifting', 'Gifting', [
  ["Your girlfriend deserves this!", "Aapki girlfriend ye deserve karti hai!"],
  ["Your boyfriend needs this too", "Aapke boyfriend ko bhi ye chahiye"],
  ["A gift he will actually use", "Aisa gift jo wo sach mein use karega"],
  ["Mom deserves a better gift", "Mummy ko achha gift toh banta hai"],
  ["Gift this to your sister", "Apni didi ko ye gift karo"],
  ["Best friend's birthday, sorted", "Bestie ke birthday ka gift sorted"],
  ["Stop gifting boring stuff", "Ab boring gift dena band karo"],
  ["Send this to the one who needs it", "Ye usko bhejo jise ye chahiye"],
  ["Surprise someone today", "Aaj kisi ko surprise de do"],
  ["The gift she will not forget", "Ye gift wo kabhi nahi bhoolegi"],
 ]),
 ('couple', 'Couples and love', [
  ["For the one you love", "Aapke special person ke liye"],
  ["Anniversary gift idea", "Anniversary ke liye gift idea"],
  ["Valentine's gift, sorted", "Valentine's ka gift sorted hai"],
  ["Surprise your partner", "Apne partner ko surprise do"],
  ["Tag your special person", "Apne special person ko tag karo"],
  ["Make her smile today", "Aaj use khush kar do"],
  ["He will love this", "Use ye bahut pasand aayega"],
 ]),
 ('pov', 'POV', [
  ["POV: you found the cutest thing", "POV: aapko sabse cute cheez mil gayi"],
  ["POV: your friend sees this on your desk", "POV: dost ne desk par ye dekh liya"],
  ["POV: Mummy finds this in your cart", "POV: Mummy ne aapke cart mein ye dekha"],
  ["POV: you nailed the gift this time", "POV: is baar gift ekdum perfect nikla"],
  ["POV: you needed this all along", "POV: ye toh kab se chahiye tha"],
  ["POV: she opens your gift", "POV: wo aapka gift khol rahi hai"],
  ["POV: your room finally looks cute", "POV: aapka room ab cute lag raha hai"],
 ]),
 ('funny', 'Funny desi', [
  ["Mummy: another purchase? Me: yes", "Mummy: phir shopping? Main: haan"],
  ["Bro asked for it, now what", "Bhai ne maang liya, ab kya karun"],
  ["My wallet vs this cute thing", "Mera wallet vs ye cute cheez"],
  ["Adding to cart at 2 AM", "Raat ke 2 baje cart mein daal diya"],
  ["Not me buying this again", "Phir se le aaya main, kya karun"],
  ["Me: no more shopping. Also me:", "Main: ab shopping nahi. Phir main:"],
  ["Salary came, cart is ready", "Salary aayi, cart pehle se ready hai"],
  ["Do not tell Mummy about this", "Mummy ko mat batana"],
 ]),
 ('hook', 'Stop scrolling', [
  ["Stop scrolling, you need this", "Ruko, scroll mat karo, ye dekho"],
  ["Save this before you forget", "Save kar lo, warna bhool jaoge"],
  ["Everyone is buying this", "Aajkal sab ye hi le rahe hain"],
  ["Found it so you do not have to", "Maine dhundh liya, aap bas le lo"],
  ["This did not go viral for nothing", "Ye yun hi viral nahi hua"],
  ["Trending right now", "Abhi sabse zyada trending"],
  ["Hidden gem alert", "Ek hidden gem mil gaya"],
 ]),
 ('curious', 'Curiosity and wait for it', [
  ["Wait for it...", "Ruko zara, aage dekho..."],
  ["Did you know this existed?", "Aapko pata tha ye bhi aata hai?"],
  ["You won't believe what this does", "Yakeen nahi hoga ye kya karta hai"],
  ["Watch till the end", "End tak zaroor dekhna"],
  ["Wait, what is this?!", "Ruko, ye kya hai?!"],
  ["Guess what this is", "Guess karo ye kya hai"],
  ["Nobody told me about this", "Mujhe kisi ne bataya hi nahi"],
 ]),
 ('confess', 'Confession and proof', [
  ["I did not expect this to be so good", "Sach bolun? Itna achha expect nahi tha"],
  ["Everyone asks where I got this", "Sab pooch rahe hain kahan se liya"],
  ["Bought for one reason, loved for more", "Ek wajah se liya, ab har wajah se pasand"],
  ["Don't tell anyone, but...", "Kisi ko batana mat, lekin..."],
  ["Small find, big smile", "Chhoti si find, badi si smile"],
  ["Okay, I get the hype now", "Ab samajh aaya hype kyun hai"],
 ]),
 ('self', 'Treat yourself', [
  ["You deserve this", "Aap ye deserve karte ho"],
  ["Treat yourself today", "Aaj khud ko treat de do"],
  ["Self-love, but make it cute", "Self-love, thoda cute wala"],
  ["Add this to your wishlist", "Isse apni wishlist mein daal lo"],
  ["Your sign to buy it", "Ye lo, lene ka sign mil gaya"],
 ]),
 ('desk', 'Desk, room and aesthetic', [
  ["Desk setup, upgraded", "Desk setup ab upgrade ho gaya"],
  ["Cute room corner", "Room ka cute corner"],
  ["Aesthetic find of the day", "Aaj ki sabse aesthetic find"],
  ["Your desk needed this", "Aapke desk ko ye chahiye tha"],
  ["This makes any corner cute", "Isse har corner cute lagta hai"],
  ["Instagram-worthy find", "Ekdum Instagram-worthy find"],
 ]),
 ('debate', 'Questions and comments', [
  ["Keep it or return it?", "Aap rakhoge ya return karoge?"],
  ["Rate this out of 10", "Isko 10 mein se kitne doge?"],
  ["Tag someone who needs this", "Jisko ye chahiye use tag karo"],
  ["Save this for your next shopping", "Agli shopping ke liye save kar lo"],
  ["Comment if you want this", "Chahiye toh comment karo"],
  ["Would you buy this?", "Aap ye kharidoge?"],
  ["Yes or no?", "Haan ya na?"],
 ]),
 ('festive', 'Festive and seasonal', [
  ["Diwali gift sorted", "Diwali ka gift sorted hai"],
  ["Festive mood: on", "Festive mood on hai"],
  ["Cute gift for Bhai Dooj", "Bhai Dooj ke liye cute gift"],
  ["Karwa Chauth gift idea", "Karwa Chauth ke liye gift idea"],
  ["Wedding season find", "Shaadi season ki best find"],
  ["Rakhi gift idea", "Rakhi ke liye gift idea"],
  ["Secret Santa idea", "Secret Santa ke liye perfect"],
  ["Diwali gifting made easy", "Diwali ka gift ab easy hai"],
 ]),
 ('pet', 'Pet', [
  ["Your pet deserves this", "Aapka pet ye deserve karta hai"],
  ["My dog approved this", "Mere dog ne ye approve kar diya"],
  ["Pet parents, you need this", "Pet parents, ye aapke liye hai"],
  ["Cat approved. Finally.", "Cat ne approve kar diya, finally"],
  ["Your furry baby will love it", "Aapke furry baby ko ye pasand aayega"],
  ["A must-have for pet parents", "Har pet parent ke liye must-have"],
 ]),
 ('price', 'Price lines (only when price is ON)', [
  ["Budget-friendly gift", "Budget mein perfect gift"],
  ["Cheaper than your coffee", "Aapki coffee se bhi sasta"],
  ["Pocket-friendly find", "Pocket ke liye ekdum sahi"],
  ["Yes, it is this affordable", "Haan, sach mein itna sasta hai"],
  ["Big joy, small price", "Kam price, zyada khushi"],
 ]),
 ('cta', 'Call to action (full lines)', [
  ["Comment LINK, get it in your DM", "LINK comment karo, DM mein mil jayega"],
  ["Comment LINK and check your DM", "LINK comment karo, phir DM check karo"],
  ["Want it? Comment LINK", "Chahiye? Comment mein LINK likho"],
  ["Comment LINK, link lands in your DM", "LINK comment karo, link DM mein aayega"],
  ["Follow for more finds like this", "Aise aur finds ke liye follow karo"],
  ["Shop link in bio", "Shop link bio mein hai"],
 ]),
]


def _lang(lang=None):
    v = (lang if lang is not None else os.getenv("TEXT_LANG", "both")).strip().lower()
    return v if v in ("english", "hinglish", "both") else "both"


def hook_ideas(lang=None, show_price=False, n=3, pet=False):
    """n viral line ideas from n different categories, in English, Hinglish or a mix."""
    lang = _lang(lang)
    cats = [c for c in TEXT_LIBRARY if c[0] != "cta" and (show_price or c[0] != "price") and (pet or c[0] != "pet")]
    out = []
    for cid, title, lines in random.sample(cats, min(n, len(cats))):
        en, hi = random.choice(lines)
        out.append(en if lang == "english" else hi if lang == "hinglish" else random.choice([en, hi]))
    return out


PRICE_OFF = ("PRICE RULE: do NOT mention any price, cost, discount, offer, 'cheap', 'cheapest' or 'budget' anywhere "
             "(no rupee amounts, no Rs, no numbers about money).")
PRICE_ON = ("PRICE RULE: you MAY mention the price once (one spoken line or the caption), but ONLY if it is clearly visible on the "
            "product page. Never invent, round or guess a price and never say 'cheapest'.")
PRICE_CAPTION = {False: "Do not mention the price. ", True: "You may mention the verified price once, never a guessed one. "}


def price_rule(on, price=""):
    """The price rule for the AI. price = the number read from the product page (digits only, e.g. "199"), "" = none was found.
    ON but no verified price -> the AI must not mention one (it must never guess)."""
    price = str(price or "").strip()
    if not on:
        return PRICE_OFF
    if not price:
        return ("PRICE RULE: the price could not be read from the product page, so do NOT mention any price, cost or number about money. "
                "Never guess one.")
    return ("PRICE RULE: the verified price on the product page is Rs %s. Mention it ONCE (in one spoken line or in the caption), "
            "say it as '%s rupaye' (or 'Rs %s' in the caption), use exactly this number and never round or change it. "
            "Never say 'cheapest' or 'discount'. It is fine to skip it if it does not fit the line naturally." % (price, price, price))


def price_caption(on, price=""):
    price = str(price or "").strip()
    if not on or not price:
        return PRICE_CAPTION[False]
    return "You may mention the verified price once: Rs %s (exactly this number, never a guessed one). " % price

# groups used by the control page to open the style list section by section
GROUP_ORDER = ["Story & talk", "Funny & POV", "Gifting", "Festive", "Aesthetic & reveal", "ASMR & satisfying",
               "Animated", "Pet", "How-to & facts"]
GROUP_OF = {
    "classic": "Story & talk", "voice_note": "Story & talk", "narrator_reveal": "Story & talk", "honest_review": "Story & talk",
    "comedy_skit": "Funny & POV", "pov_skit": "Funny & POV", "mummy_vs_me": "Funny & POV", "rate_it": "Funny & POV",
    "text_pov": "Funny & POV", "wallet_joke": "Funny & POV",
    "gift_reveal": "Gifting", "unboxing_smile": "Gifting", "gift_hint": "Gifting", "gift_wrapup": "Gifting",
    "festive_greeting": "Festive", "diwali_sparkle": "Festive",
    "lofi_showoff": "Aesthetic & reveal", "quiet_closeup": "Aesthetic & reveal", "lifestyle_vibe": "Aesthetic & reveal",
    "dramatic_reveal": "Aesthetic & reveal", "glow_up": "Aesthetic & reveal", "cosy_room": "Aesthetic & reveal",
    "colour_flatlay": "Aesthetic & reveal",
    "asmr_hands": "ASMR & satisfying", "satisfying_loop": "ASMR & satisfying", "unboxing_asmr": "ASMR & satisfying",
    "product_speaks": "Animated", "jingle": "Animated", "claymation": "Animated", "cartoon_3d": "Animated",
    "stopmotion_pack": "Animated",
    "pet_play": "Pet", "pet_storyteller": "Pet", "pet_inner_voice": "Pet", "pet_owner_voice": "Pet", "pet_asmr": "Pet",
    "pet_loop": "Pet", "pet_cosy": "Pet", "pet_reaction": "Pet",
    "howto_steps": "How-to & facts", "did_you_know": "How-to & facts",
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

# ---- NEW formats (viral-style: gifting, POV, reveal transitions, festive, rating, did-you-know) ----
# with a voice (two spoken lines, about 13-16 words). They reuse the hook types above; the brief carries the specifics.
FORMATS += [
    _f("gift_hint", "gift hint (\"she deserves this\")", "talk", "gift_hint", 1.6, "product", dialogue=True, use_base_style=True,
       hook_types=["gift_moment", "confession", "mini_story", "pov"], answer_rule=False,
       brief="a warm, slightly teasing GIFT HINT. Line 1 speaks straight to the viewer about someone they love (girlfriend, boyfriend, "
             "Mummy, Didi, Bhai or bestie), in the spirit of 'your girlfriend deserves this'. Line 2 shows the product as the thoughtful "
             "gift and the one small reason it feels special. Sweet, never pushy."),
    _f("pov_skit", "POV skit", "talk", "pov", 1.5, "product", dialogue=True, use_base_style=True,
       hook_types=["pov", "mini_story", "funny_exaggeration", "relatable_pain"], answer_rule=False,
       brief="a POV micro-skit. Line 1 starts with 'POV' (or 'Jab...') and puts the viewer inside a very relatable moment, line 2 is a "
             "short twist where the product quietly appears. Feels like a friend's reel, not an ad."),
    _f("mummy_vs_me", "Mummy vs me", "talk", "comedy", 1.4, "product", dialogue=True, use_base_style=True,
       hook_types=["mummy_reaction", "funny_exaggeration", "mini_story"], answer_rule=False,
       brief="a funny desi exchange. Line 1 = what Mummy (or Bhai, Didi) says about the buying habit, line 2 = the cheeky answer "
             "in which the product is the reason. Light family humour, nobody is insulted."),
    _f("honest_review", "honest first reaction", "talk", "review", 1.3, "product", dialogue=True, use_base_style=True,
       hook_types=["confession", "surprise_detail", "curiosity_gap"], answer_rule=False,
       brief="a casual 'I did not expect this' voice-note reaction. Line 1 = what you thought before, line 2 = the one real thing "
             "that surprised you. Only say things the product page clearly supports."),
    _f("rate_it", "rate it / keep or return", "talk", "engage", 1.3, "product", dialogue=True, use_base_style=True,
       hook_types=["curiosity_gap", "funny_exaggeration", "confession"], answer_rule=False,
       brief="an engagement video. Line 1 asks the viewer a fun question (rate it out of 10, keep it or return it), line 2 reveals "
             "one honest detail of the product and invites them to answer in the comments."),
    _f("festive_greeting", "festive gift line", "narrator", "festive", 1.2, "product", dialogue=True, use_base_style=True,
       hook_types=["gift_moment", "mini_story", "narrator_tease"], answer_rule=False,
       voice_line="Voice: warm, cheerful festive storyteller narrator, speaking everyday Hindi." + _PRON,
       brief="a warm festive narrator (Diwali, Bhai Dooj, wedding season or any celebration). Line 1 sets a happy festive moment, "
             "line 2 gives the product as the small gift that makes it special."),
    _f("did_you_know", "did you know?", "narrator", "facts", 1.2, "product", dialogue=True, use_base_style=True, needs_facts=True,
       hook_types=["surprise_detail", "curiosity_gap"], answer_rule=False,
       voice_line="Voice: calm, friendly narrator, speaking everyday Hindi." + _PRON,
       brief="a 'did you know?' fact reel. Line 1 teases a small surprise, line 2 reveals ONE real feature from the VERIFIED FACTS. "
             "No claim that the facts do not support."),
    _f("wallet_joke", "wallet vs product (cartoon)", "talk", "comedy", 1.2, "none", dialogue=True, loose=True, answer_rule=False,
       hook_types=["funny_exaggeration", "confession"],
       voice_line="Voice: playful, friendly shopper voice, speaking everyday Hindi." + _PRON,
       scene_rule="the product from the reference image, SAME shape and colours, as the cute hero of a tiny colourful cartoon desk "
                  "world, bouncing happily, no people, no text on screen.",
       core="Vertical 10-second cute 3D cartoon: the product bounces happily in a tiny colourful desk world.",
       brief="a funny 'my wallet vs this product' voice. Line 1 = the playful shopper guilt ('I was only browsing'), line 2 = the "
             "cheeky excuse that the product is too cute to skip. Light humour, no selling."),
]
# without a voice (music or sounds only)
FORMATS += [
    _f("dramatic_reveal", "dramatic reveal transition", "music", "reveal", 1.5, "product",
       audio="Voice: NO voice, no talking. Music: a build-up that drops with a short whoosh at the reveal.",
       core="Vertical 10-second dramatic reveal: a plain dull scene, then a snap-cut transition reveals the product in a bright, beautiful shot.",
       plan="a dramatic reveal transition: a plain, slightly dull scene, then a quick snap-cut that reveals the product in a bright, beautiful hero shot.",
       shots="A plain dull scene with the {p} barely visible; a snap cut with a whoosh; the {p} glows in a bright hero shot."),
    _f("text_pov", "silent POV moment", "music", "pov", 1.2, "product",
       audio="Voice: NO voice, no talking. Music: soft trendy lo-fi beat.",
       core="Vertical 10-second silent relatable POV moment with the product, handheld feel. No words appear in the video itself.",
       plan="a relatable POV moment acted out silently (the viewer's point of view) where the product is quietly the hero; the video itself shows NO text, any words are added later.",
       shots="A handheld POV moment; the {p} comes into view; a small happy reaction; end on the {p}."),
    _f("gift_wrapup", "gift wrap-up", "music", "gift_wrap", 1.2, "hands",
       audio="Voice: NO voice, no talking. Music: warm happy instrumental with soft paper sounds.",
       core="Vertical 10-second gift wrap-up: an adult's hands place the product in a small gift box, tie a ribbon and add a plain tag.",
       plan="a satisfying gift wrap-up: adult hands place the product in a small plain gift box, tie a ribbon, add a blank tag card and slide it toward the camera.",
       shots="Hands place the {p} in a small box; a ribbon is tied; the finished gift slides toward the camera."),
    _f("glow_up", "before and after glow-up", "music", "beforeafter", 1.2, "hands",
       audio="Voice: NO voice, no talking. Music: upbeat transformation beat.",
       core="Vertical 10-second glow-up: a plain corner becomes nicer when adult hands place the product in it.",
       plan="a before and after: a plain, slightly dull corner, then adult hands place the product and the same corner looks nicer; only things the product can really do.",
       shots="A plain corner; hands place the {p}; the same corner now looks brighter and nicer."),
    _f("cosy_room", "cosy room vibe", "music", "cosy", 1.2, "product",
       audio="Voice: NO voice, no talking. Music: cosy warm lo-fi instrumental.",
       core="Vertical 10-second cosy room vibe: warm fairy lights, soft shadows, the product in a calm corner, slow camera.",
       plan="a cosy room vibe: warm fairy lights and soft shadows, the product sits (or is used the way it is meant to be) in a calm corner, slow camera.",
       shots="Warm fairy lights glow; a slow move toward the {p}; end on a calm full view."),
    _f("unboxing_asmr", "unboxing ASMR", "asmr", "satisfying", 1.2, "hands",
       audio="Voice: NO voice, NO music. Sound: only close-up unboxing sounds (tape, paper, box, soft taps).",
       core="Vertical 10-second unboxing ASMR: an adult's hands slowly open a plain box and lift out the product, soft window light.",
       plan="unboxing ASMR: adult hands slowly cut the tape, open a plain box, lift out the product and set it down; every sound is close and satisfying.",
       shots="Hands open a plain box; the {p} is slowly lifted out; it is set down and turned once."),
    _f("colour_flatlay", "colour-pop flat lay", "music", "aesthetic", 1.1, "product",
       audio="Voice: NO voice, no talking. Music: playful beat, product moves land on the beat.",
       core="Vertical 10-second colour-pop flat lay: top-down view of the product on a bright coloured backdrop, moves landing on the beat.",
       plan="a top-down colour-pop flat lay: the product on a bright coloured backdrop with a few simple props, cuts and moves landing on the beat.",
       shots="Top-down view of the {p} on a bright backdrop; a quick beat-synced reposition; end on a neat full view."),
    _f("stopmotion_pack", "stop-motion pop-in", "music", "animation", 1.1, "none", loose=True,
       audio="Voice: NO voice, no talking. Music: playful stop-motion style tune.",
       core="Vertical 10-second stop-motion pop-in: little props pop in around the product on the beat, then the product spins once.",
       plan="a stop-motion pop-in where small colourful props pop in around the product (real shape and colours) on the beat, ending with one spin.",
       shots="Small props pop in around the {p} on the beat; the {p} spins once; it settles with a tiny bounce."),
    _f("diwali_sparkle", "festive sparkle", "music", "festive", 1.1, "product",
       audio="Voice: NO voice, no talking. Music: warm festive instrumental.",
       core="Vertical 10-second festive sparkle: warm golden lights, soft glow and shimmer around the product, slow camera.",
       plan="a festive sparkle: warm golden fairy lights and a soft glow around the product, gentle shimmer, slow camera; no fire or real flames.",
       shots="Golden lights twinkle; a slow move toward the {p}; a soft shimmer; end on the full view."),
    _f("pet_reaction", "pet first reaction", "music", "pet_reaction", 1.5, "pet",
       audio="Voice: NO voice, no talking. Music: playful cheerful instrumental, soft happy pet sounds.",
       core="Vertical 10-second pet first reaction: a healthy adult pet meets the product, curious then happy, warm daylight.",
       plan="a pet's first reaction: a healthy adult pet notices the product, is curious, then happily uses it exactly the way it is meant to be used; only real, possible actions.",
       shots="The pet spots the {p}; it sniffs it curiously; a happy close-up of the pet with the {p}."),
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
    """Every format with the video types it fits, whether it has a voice and its group (used by the control page dropdown)."""
    out = []
    for f in FORMATS:
        out.append({"id": f["id"], "label": f["label"], "voice": f["voice"],
                    "audible": VOICE_PROPS[f["voice"]][0] != "none",
                    "group": GROUP_OF.get(f["id"], "Story & talk"),
                    "types": [t for t, fn in VIDEO_TYPES.items() if fn(f)]})
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    out.sort(key=lambda x: order.get(x["group"], 99))        # stable: keeps the original order inside a group
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


def pick(p, r, product_mode="none", kids=False, hist=None, video_type=None, want_cta=None, adult_angle=False, audio=None, forced_format=None, show_price=None, lang=None, price=""):
    """Choose the style of the next reel. p, r are the product and the research dict (r["features"] = verified features).
    kids + adult_angle = a children's product that is shown with ADULTS only (the people choice is kept).
    audio = "voice" (someone talks / narrates / sings) | "none" (music or sounds only) | "" (any). Default: AUDIO_MODE from the environment.
    show_price = True / False: may the video or caption mention the price? Default: SHOW_PRICE from the environment (off when unset).
    lang = "english" | "hinglish" | "both": language of the viral line ideas. Default: TEXT_LANG from the environment (both).
    price = the verified price from the product page (digits, "" = none read): only used when show_price is on."""
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
    sp = bool(show_price if show_price is not None else _flag("SHOW_PRICE", False))
    lang_used = _lang(lang)
    ideas = hook_ideas(lang_used, bool(sp and str(price or "").strip()), pet=(product_mode == "pet"))      # price lines only when a verified price exists
    trend = read_trends()
    if ideas:
        trend = ((trend + " | ") if trend else "") + "Viral line ideas (optional, adapt to the product, skip if they do not fit): " + " / ".join(ideas)
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
        "ad_brief": AD_BRIEF[ad] + " " + price_rule(sp, price),
        "caption_tone": AD_CAPTION[ad] + price_caption(sp, price), "caption_cta": cta,
        "show_price": sp, "price": (str(price or "").strip() if sp else ""), "text_lang": lang_used, "hook_ideas": ideas,
        "setting": setting, "camera": camera, "mood": mood, "trend": trend,
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
        if i == 0:
            print("    ideas:", s["hook_ideas"], "| price:", s["show_price"], "| lang:", s["text_lang"])
        print("%2d) %-16s voice=%-8s job=%-12s ad=%-6s spoken_cta=%-5s check=%-8s | %s | %s | %s"
              % (i + 1, s["format"], s["voice"], s["job"], s["ad"], s["spoken_cta"], s["voice_check"],
                 s["setting"], s["camera"], s["mood"]))
        h.append({k: s.get(k) for k in ("format", "voice", "job", "ad", "setting", "camera", "mood", "cta")})
        h = h[-KEEP:]
