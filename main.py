import csv, os, subprocess, sys, time, traceback
import phone, agent, verify, caption, notify, find_product, trim, yt_flow
from config import PKG, SHORT_PROMPT_STYLE

# --- safety patch: the AI sometimes answers numbers as text ("738" instead of 738) ---
_orig_tap = phone.tap_norm
def _safe_tap(d, x, y):
    return _orig_tap(d, int(round(float(x))), int(round(float(y))))
phone.tap_norm = _safe_tap

_orig_decide = agent.decide
def _safe_decide(*a, **k):
    r = _orig_decide(*a, **k)
    if isinstance(r, dict):
        for key in ("x", "y"):
            if key in r:
                r[key] = max(0, min(1000, int(round(float(r[key])))))
        if "seconds" in r:
            r["seconds"] = int(float(r["seconds"]))
    return r
agent.decide = _safe_decide

STYLE = (SHORT_PROMPT_STYLE or "").strip().strip('"').strip("'")
if STYLE.upper().startswith("SHORT_PROMPT_STYLE="):
    STYLE = STYLE.split("=", 1)[1].strip()

# Comment-for-link line added to every caption (change it with CAPTION_CTA in .env)
CTA = os.getenv("CAPTION_CTA", "Link chahiye? Comment mein 'Link' likho, link seedha DM mein mil jayega.")

BASE = os.path.dirname(os.path.abspath(__file__))
VID_DIR = os.path.join(BASE, "videos")
CSV = os.path.join(BASE, "products.csv")

def load_products():
    with open(CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_products(rows):
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "description", "status"])
        w.writeheader(); w.writerows(rows)

def step(name, ok_msg):
    ok, why = ok_msg
    print(f"[{'OK' if ok else 'FAIL'}] {name}: {why}")
    if not ok:
        raise RuntimeError(f"{name} failed: {why}")

def create_and_download(d, p, shot_remote):
    """YouTube Create via yt_flow (fixed, verified UI steps, no AI) -> exported mp4 -> copied to the laptop."""
    prompt = f"{STYLE} Product: {p['name']}. Details: {p['description']}"
    remote = yt_flow.run(d, prompt)
    print("exported video:", remote)
    local = os.path.join(VID_DIR, f"{int(time.time())}.mp4")
    phone.adb("pull", remote, local)
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        raise RuntimeError("video was not copied to the laptop: " + remote)
    return local

def run():
    os.makedirs(VID_DIR, exist_ok=True)
    d = phone.connect()
    try:
        # 1. AI finds an affiliate product on Meesho by itself
        p, png = find_product.find_affiliate_product(d)
        notify.send(f"Picked product: {p['name']}")
        shot_local = os.path.join(VID_DIR, "product.png")
        open(shot_local, "wb").write(png)

        # 2-4. Create, download, verify (one retry)
        info = None
        for attempt in (1, 2):
            # push the reference image fresh each attempt: yt_flow picks the NEWEST item in the picker
            shot_remote = f"/sdcard/Pictures/product_shot_{int(time.time())}.png"
            phone.push_media(shot_local, shot_remote)
            phone.adb("shell", "touch", shot_remote)
            time.sleep(2)
            video = create_and_download(d, p, shot_remote)
            video = trim.trim_reference_end(video, shot_local)   # cut the screenshot YouTube adds at the end
            info = verify.check_video(video, p)
            print("verify:", info)
            if info.get("pass"):
                break
            notify.send(f"Verify failed (attempt {attempt}): {info.get('reason')}")
        if not info or not info.get("pass"):
            raise RuntimeError("video failed verification twice, skipping today")

        # 5. Caption
        cap = caption.make_caption(p, info)
        if CTA.lower() not in cap.lower():
            cap = cap.rstrip() + " " + CTA

        # 6. Post Reel
        remote_video = "/sdcard/Movies/reel_today.mp4"
        phone.push_media(video, remote_video)
        phone.open_app(d, PKG["instagram"])
        step("instagram post", agent.run_task(d,
            f"In Instagram, create a new Reel using the video 'reel_today.mp4' (the most recent video in the gallery). "
            f"Proceed through next screens without adding music/effects, and when asked for the caption type exactly: "
            f"\"{cap}\". Then tap Share. Done when the Reel is shared/uploading confirmation appears.", max_steps=45, key="instagram_post", texts=[cap]))
        find_product.mark_posted(p["name"])     # Reel is live: never post this product again
        time.sleep(30)

        # 7. Meesho Creator Club auto-DM
        phone.open_app(d, PKG["meesho"])
        step("creator club", agent.run_task(d,
            f"In the Meesho app open Creator Club and find the auto DM option. Select the Instagram post/reel I just "
            f"posted (the newest one) for the product '{p['name']}' and confirm so the auto DM is linked to that post. "
            f"If it asks for a comment keyword / trigger word, use exactly: Link. "
            f"Done when it shows the auto DM is active/linked to the post.", max_steps=50, key="creator_club"))

        notify.send(f"Posted: {p['name']}")
    except Exception as e:
        notify.send(f"FAILED: {e}")
        with open(os.path.join(BASE, "logs", "errors.log"), "a") as f:
            f.write(traceback.format_exc() + "\n")
        try:
            open(os.path.join(BASE, "logs", f"fail_{int(time.time())}.png"), "wb").write(phone.screenshot(d))
        except Exception:
            pass
        sys.exit(1)
    finally:
        try: d.quit()
        except Exception: pass

if __name__ == "__main__":
    run()
