import json, os, subprocess, glob
from google.genai import types
import llm

def check_video(video_path, product):
    work = os.path.dirname(video_path) or "."
    for f in glob.glob(os.path.join(work, "frame_*.jpg")):
        os.remove(f)
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vf", "fps=1/3", "-frames:v", "6",
                    os.path.join(work, "frame_%d.jpg")], capture_output=True)
    audio = os.path.join(work, "audio.mp3")
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", audio],
                   capture_output=True)
    parts = []
    for f in sorted(glob.glob(os.path.join(work, "frame_*.jpg"))):
        parts.append(types.Part.from_bytes(data=open(f, "rb").read(), mime_type="image/jpeg"))
    if os.path.exists(audio) and os.path.getsize(audio) > 1000:
        parts.append(types.Part.from_bytes(data=open(audio, "rb").read(), mime_type="audio/mp3"))
    prompt = f"""You are a QA reviewer for a short product video for: "{product['name']}" ({product['description']}).
Frames and audio are attached. Check: matches the product, no distorted faces/hands, no garbled text,
a clear voice is present and not cut off, no false claims (guaranteed, lowest price, medical claims).
Return JSON only: {{"pass": true/false, "reason": "...", "summary": "2-3 sentence description of the video", "transcript": "what the voice says"}}"""
    return json.loads(llm.generate(parts + [prompt]))
