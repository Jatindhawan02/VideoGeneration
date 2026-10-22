"""Lets the AI find an affiliate product on Meesho by itself."""
import json, os
from google.genai import types
from config import PKG
import agent, phone, llm
import time
from appium.webdriver.common.appiumby import AppiumBy
BASE = os.path.dirname(os.path.abspath(__file__))
POSTED = os.path.join(BASE, "posted.txt")

def posted_names():
    if not os.path.exists(POSTED):
        return []
    return [l.strip() for l in open(POSTED, encoding="utf-8") if l.strip()]

def mark_posted(name):
    with open(POSTED, "a", encoding="utf-8") as f:
        f.write(name + "\n")

def read_product(png):
    prompt = """This is a Meesho product page screenshot. Return JSON only:
{"name": "product name", "description": "one short factual line about the product from the page",
 "has_affiliate_tag": true/false, "commission_percent": number or null}
has_affiliate_tag is true only if the page visibly shows a commission, earn, share-and-earn, or creator/affiliate marker.
commission_percent is the commission percentage shown on the page (for example 5 for 5%), or null if not visible."""
    return json.loads(llm.generate([types.Part.from_bytes(data=png, mime_type="image/png"), prompt]))


WL_BUTTON = "com.meesho.supply:id/wishlist"      # the product's own Wishlist button
WL_LABEL = "com.meesho.supply:id/tv_label"       # its text: "Wishlist" -> "Wishlisted" once saved

def wishlist_state(d):
    """'on' (already wishlisted), 'off', or None if the button is not on screen. Read from the UI, no AI."""
    try:
        for btn in d.find_elements(AppiumBy.ID, WL_BUTTON):
            try:
                label = btn.find_element(AppiumBy.ID, WL_LABEL).text.strip().lower()
            except Exception:
                continue
            return "on" if "wishlisted" in label else "off"
    except Exception:
        pass
    return None

def add_to_wishlist(d):
    """Tap the product's Wishlist button and CONFIRM from the UI that it now says 'Wishlisted'."""
    state = wishlist_state(d)
    for _ in range(3):                       # button may be below the fold
        if state is not None:
            break
        phone.swipe(d, "up")
        time.sleep(1.5)
        state = wishlist_state(d)
    if state is None:
        print("  wishlist button not found on this page")
        return False
    if state == "on":
        print("  product was already in the wishlist")
        return True
    for attempt in range(2):
        try:
            d.find_element(AppiumBy.ID, WL_BUTTON).click()
        except Exception as e:
            print("  could not tap the Wishlist button:", str(e)[:80])
            return False
        time.sleep(2)
        if wishlist_state(d) == "on":
            print("  wishlist confirmed: button now says Wishlisted")
            return True
    print("  tapped, but the button did not change to Wishlisted")
    return False

def save_debug(d, name):
    try:
        os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
        with open(os.path.join(BASE, "logs", name + ".png"), "wb") as f:
            f.write(phone.screenshot(d))
        with open(os.path.join(BASE, "logs", name + "_ui.xml"), "w", encoding="utf-8") as f:
            f.write(d.page_source)
        print(f"  saved logs/{name}.png and logs/{name}_ui.xml")
    except Exception as e:
        print("  could not save debug files:", str(e)[:80])

def find_affiliate_product(d, tries=4):
    """Returns (product_dict, screenshot_png) or raises."""
    done = posted_names()
    for attempt in range(1, tries + 1):
        phone.open_app(d, PKG["meesho"])
        goal = (
            "In the Meesho app, browse the normal HOME screen and categories (or use search with ideas like "
            "'cute gift', 'trendy accessories', 'aesthetic home decor', 'cute stationery', 'mini gadgets') to find ONE cute, "
            "eye-catching product that could go viral on Instagram. Do NOT open the profile menu or Creator Club for "
            "finding the product. Open a product page and look for the commission / earning percentage "
            "(often shown near the Share or 'Earn' button). The commission must be ABOVE 2%. "
            "If the product shows no commission or 2% or less, go back and try another product. "
            f"Do NOT choose any of these already-used products: {done[-30:]}. "
            "Done when a cute product page is open and its commission percentage above 2% is visible.")
        ok, why = agent.run_task(d, goal, max_steps=45)
        if not ok:
            print("find attempt failed:", why)
            continue
        png = phone.screenshot(d)
        info = read_product(png)
        print("found:", info)
        pct = info.get("commission_percent")
        if (info.get("has_affiliate_tag") and info.get("name") and info["name"] not in done
                and pct is not None and float(pct) > 2):
            # add to wishlist as the user wants (checked from the UI, not by AI)
            if add_to_wishlist(d):
                print("wishlist: done and confirmed")
            else:
                print("wishlist: NOT confirmed (continuing with the rest)")
                save_debug(d, "wishlist_fail")
            return {"name": info["name"], "description": info.get("description", "")}, png
    raise RuntimeError("could not find a cute product with commission above 2% after several tries")
