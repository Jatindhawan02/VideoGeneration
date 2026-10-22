"""Check a finished reel: look at real frames and listen to the real audio.

check_video(video_path, product, script) -> dict with
  pass, reason, transcript, summary, checks (True / False / None), unchecked (list)

How the audio is checked (the old version sent audio to AI models that cannot hear, so they always
answered "audio not provided" and the video failed):
  1. The audio is turned into text by a real speech-to-text model (Groq Whisper, needs GROQ_API_KEY).
  2. Voice language and the "comment LINK" line are checked on that text locally, no AI.
  3. The 8 frames are joined into ONE picture and, with the text, sent to the normal AI to judge the visuals.
A check that could not be done is None (not checked). It is NOT a failure and never triggers a re-make.
Needs ffmpeg + ffprobe on PATH.
"""
import io, json, os, re, subprocess, tempfile, time
import requests
from PIL import Image
from google.genai import types
import llm
from script_writer import CTA

N_FRAMES = 8
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")


def _run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _duration(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path], 60)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _has_audio(path):
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
              "-of", "csv=p=0", path], 60)
    return "audio" in r.stdout


def _frames(path, tmp, dur):
    out = []
    for i in range(N_FRAMES):
        t = dur * (i + 0.5) / N_FRAMES
        f = os.path.join(tmp, "f%d.jpg" % i)
        _run(["ffmpeg", "-y", "-ss", "%.2f" % t, "-i", path, "-frames:v", "1",
              "-vf", "scale=480:-2", "-q:v", "5", f], 60)
        if os.path.exists(f) and os.path.getsize(f) > 0:
            out.append(open(f, "rb").read())
    return out


def _collage(frames, cols=4, tw=300):
    """All frames in ONE picture (time order: left to right, top to bottom). Every AI provider accepts one image."""
    ims = [Image.open(io.BytesIO(b)).convert("RGB") for b in frames]
    ims = [im.resize((tw, max(1, int(im.height * tw / im.width)))) for im in ims]
    rows = [ims[i:i + cols] for i in range(0, len(ims), cols)]
    th = max(im.height for im in ims)
    canvas = Image.new("RGB", (cols * tw, len(rows) * th), (0, 0, 0))
    for r, row in enumerate(rows):
        for c, im in enumerate(row):
            canvas.paste(im, (c * tw, r * th))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _audio(path, tmp):
    f = os.path.join(tmp, "a.mp3")
    _run(["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", f], 120)
    return f if os.path.exists(f) and os.path.getsize(f) > 0 else None


def _transcribe(audio_path):
    """Speech to text with Groq Whisper. Returns (text or None, note). None = could not be done."""
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        return None, "no GROQ_API_KEY, so the voice could not be turned into text"
    for attempt in range(3):
        try:
            with open(audio_path, "rb") as f:
                r = requests.post(
                    WHISPER_URL, headers={"Authorization": "Bearer " + key},
                    files={"file": ("audio.mp3", f, "audio/mpeg")},
                    # Hindi words come back in Devanagari and English words in Latin letters, the same mixed
                    # script the voice was given (so the word-by-word check compares like with like).
                    # The prompt is a generic sample, NOT the real script, so it cannot make a wrong voice look right.
                    data={"model": WHISPER_MODEL, "response_format": "json", "temperature": "0", "language": "hi",
                          "prompt": "ये एक product video का voiceover है। Hindi और English mix, जैसे: यह toy बहुत cute है, "
                                    "Link चाहिए? Comment LINK, DM में मिल जाएगा।"},
                    timeout=120)
        except Exception as e:
            return None, "speech-to-text network error: " + str(e)[:80]
        if r.status_code == 200:
            try:
                return (r.json().get("text") or "").strip(), "ok"
            except Exception:
                return None, "speech-to-text gave an unreadable answer"
        if r.status_code == 429:
            time.sleep(min(20 * (attempt + 1), 60))
            continue
        return None, "speech-to-text error %d: %s" % (r.status_code, r.text[:100])
    return None, "speech-to-text is rate limited right now"


# --------------------------- local checks on the transcript (no AI) ---------------------------
_HINDI_WORDS = {"hai", "hain", "mein", "ye", "yeh", "yah", "ke", "ki", "ka", "ko", "se", "aur", "bahut", "nahi",
                "kya", "ek", "apne", "aap", "tum", "dekho", "chahiye", "milega", "mil", "jayega", "jaayega",
                "liye", "wala", "wali", "sabse", "bhi", "toh", "karo", "lo", "dost", "yaar", "kaisa", "tension"}
_COMMENT = ("comment", "कमेंट", "कमेन्ट", "कमैंट", "कमेण्ट", "کمنٹ")
_LINK = ("link", "लिंक", "लिन्क", "लींक", "لنک")


def _is_hinglish(t):
    if re.search(r"[\u0900-\u097F\u0600-\u06FF]", t or ""):         # Devanagari or Urdu letters = Hindi/Urdu speech
        return True
    return len(set(re.findall(r"[a-z]+", (t or "").lower())) & _HINDI_WORDS) >= 2


def _cta_in(transcript):
    """'comment' and 'link' must both be said in the last part of the voiceover (Roman or Devanagari)."""
    t = (transcript or "").lower()
    if not t:
        return False
    tail = t[int(len(t) * 0.45):]
    return any(w in tail for w in _COMMENT) and any(w in tail for w in _LINK)


def _b(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes")
    return bool(v)


def check_video(video, product, script):
    if not os.path.exists(video) or os.path.getsize(video) == 0:
        return {"pass": False, "reason": "video file missing", "transcript": "", "summary": "", "checks": {}, "unchecked": []}
    dur = _duration(video)
    if dur < 5:
        return {"pass": False, "reason": "video is too short (%.1fs)" % dur, "transcript": "", "summary": "",
                "checks": {}, "unchecked": []}
    if not _has_audio(video):
        return {"pass": False, "reason": "video has no audio / no voiceover", "transcript": "", "summary": "",
                "checks": {"has_audio": False}, "unchecked": []}
    with tempfile.TemporaryDirectory() as tmp:
        frames = _frames(video, tmp, dur)
        audio_path = _audio(video, tmp)
        transcript, note = (None, "audio could not be extracted (is ffmpeg installed?)")
        if audio_path:
            transcript, note = _transcribe(audio_path)
    if len(frames) < 3:
        return {"pass": False, "reason": "could not extract frames (is ffmpeg installed?)", "transcript": "",
                "summary": "", "checks": {}, "unchecked": []}
    if transcript is None:
        print("  (audio not checked: " + note + ")")
    else:
        print("  heard:", transcript[:200])

    heard = ('"' + transcript + '"') if transcript else "(not available - leave matches_script as null)"
    prompt = (
        "You are quality-checking an Instagram Reel made for the product below. "
        "You get ONE image: " + str(len(frames)) + " frames of the video in time order (left to right, top to bottom).\n"
        "Product: " + product["name"] + " - " + (product["description"] or "") + "\n"
        "Speech-to-text of what is really spoken in the video (Hindi words in Devanagari, English words in Latin letters, small mistakes are normal): "
        + heard + "\n"
        "Intended voiceover (Hinglish): " + script["voiceover"] + "\n"
        "The voiceover should END with: " + CTA + "\n\n"
        "Answer as JSON:\n"
        "{\"summary\": \"one line: what the video shows\", "
        "\"matches_script\": true/false/null (the spoken text follows the intended voiceover in meaning, not unrelated talk; "
        "null if the speech text is not available), "
        "\"frames_show_product\": true/false (the product from the name is clearly visible in several frames), "
        "\"frames_clean\": true/false (no black/frozen/glitchy frames, no leftover screenshot of the shopping app or UI), "
        "\"bad_text_or_claims\": true/false (price, discount, 'best', 'guaranteed' shown or spoken), "
        "\"reason\": \"if anything is wrong say what, else 'ok'\"}"
    )
    need = ["summary", "frames_show_product", "frames_clean", "bad_text_or_claims", "reason"]

    def validate(text):
        try:
            data = json.loads(text)
        except Exception:
            return "not valid JSON"
        if not isinstance(data, dict):
            return "answer must be one JSON object"
        for k in need:
            if k not in data:
                return "missing '" + k + "'"
        return None

    parts = [types.Part.from_bytes(data=_collage(frames), mime_type="image/jpeg"), prompt]
    r = json.loads(llm.generate(parts, validate=validate))

    spoke = bool(transcript) and len(transcript.split()) >= 4
    checks = {
        "has_speech": (spoke if transcript is not None else None),
        "voice_is_hinglish": (_is_hinglish(transcript) if spoke else (False if transcript is not None else None)),
        "cta_spoken": (_cta_in(transcript) if spoke else (False if transcript is not None else None)),
        "matches_script": (_b(r.get("matches_script")) if (spoke and r.get("matches_script") is not None) else None),
        "frames_show_product": _b(r["frames_show_product"]),
        "frames_clean": _b(r["frames_clean"]),
        "no_bad_claims": not _b(r["bad_text_or_claims"]),
    }
    failed = [k for k, v in checks.items() if v is False]
    unchecked = [k for k, v in checks.items() if v is None]
    if failed:
        reason = "failed: " + ", ".join(failed) + ". " + str(r.get("reason", ""))
    elif unchecked:
        reason = "ok (not checked: " + ", ".join(unchecked) + " - " + note + ")"
    else:
        reason = "ok"
    return {"pass": not failed, "reason": reason, "transcript": transcript or "",
            "summary": str(r.get("summary", "")), "checks": checks, "unchecked": unchecked}
