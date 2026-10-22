import csv, os, sys, time, traceback
import phone, agent, verify, caption, notify, find_product
from config import PKG, SHORT_PROMPT_STYLE

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
    phone.open_app(d, PKG["youtube"])
    prompt = f"{SHORT_PROMPT_STYLE} Product: {p['name']}. Details: {p['description']}"
    step("youtube create", agent.run_task(d,
        f"In the YouTube app, create a Short using the AI video generation feature. Use the latest "
        f"product screenshot image from the gallery (file {os.path.basename(shot_remote)}) as the reference image, "
        f"and type this prompt when asked: \"{prompt}\". Start generation and wait until the video is ready "
        f"(it may take several minutes, use wait). Stop when the finished video preview is visible.",
        max_steps=60, key="youtube_create", texts=[prompt]))
    step("youtube save", agent.run_task(d,
        "Save/download the generated video to the phone (not publish it publicly). "
        "Done when the video is saved to the device gallery or a 'saved' confirmation appears.", max_steps=25, key="youtube_save"))
    time.sleep(3)
    remote = None
    for folder in ["/sdcard/Movies", "/sdcard/DCIM/Camera", "/sdcard/Download", "/sdcard/DCIM"]:
        remote = phone.newest_file(folder, "mp4")
        if remote:
            break
    if not remote:
        raise RuntimeError("could not find downloaded mp4 on phone")
    local = os.path.join(VID_DIR, f"{int(time.time())}.mp4")
    phone.adb("pull", remote, local)
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
        shot_remote = "/sdcard/Pictures/product_shot.png"
        phone.push_media(shot_local, shot_remote)

        # 2-4. Create, download, verify (one retry)
        info = None
        for attempt in (1, 2):
            video = create_and_download(d, p, shot_remote)
            info = verify.check_video(video, p)
            print("verify:", info)
            if info.get("pass"):
                break
            notify.send(f"Verify failed (attempt {attempt}): {info.get('reason')}")
        if not info or not info.get("pass"):
            raise RuntimeError("video failed verification twice, skipping today")

        # 5. Caption
        cap = caption.make_caption(p, info)

        # 6. Post Reel
        remote_video = "/sdcard/Movies/reel_today.mp4"
        phone.push_media(video, remote_video)
        phone.open_app(d, PKG["instagram"])
        step("instagram post", agent.run_task(d,
            f"In Instagram, create a new Reel using the video 'reel_today.mp4' (the most recent video in the gallery). "
            f"Proceed through next screens without adding music/effects, and when asked for the caption type exactly: "
            f"\"{cap}\". Then tap Share. Done when the Reel is shared/uploading confirmation appears.", max_steps=45, key="instagram_post", texts=[cap]))
        time.sleep(30)

        # 7. Meesho Creator Club auto-DM
        phone.open_app(d, PKG["meesho"])
        step("creator club", agent.run_task(d,
            f"In the Meesho app open Creator Club and find the auto DM option. Select the Instagram post/reel I just "
            f"posted (the newest one) for the product '{p['name']}' and confirm so the auto DM is linked to that post. "
            f"Done when it shows the auto DM is active/linked to the post.", max_steps=50, key="creator_club"))

        find_product.mark_posted(p["name"])
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
