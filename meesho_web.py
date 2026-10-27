"""meesho_web.py - reads a Meesho product page in a browser ON THE PC (no phone needed).

fetch(url, out_dir) -> (carousel, info, texts)
  carousel : [(path, png_bytes), ...]  the product's own pictures (original files, not screenshots), saved as product.png, image_2.png ...
  info     : []                         (the phone version has info screenshots here; the web page gives text instead)
  texts    : [str, ...]                 the page text from the product title down to the similar-products part
It is a drop-in replacement for the phone's capture_pages() in main_link.py.

Try it alone (saves the page too, so you can send me web_page.html if something is wrong):
  python meesho_web.py "https://www.meesho.com/....../p/xxxxx" [output_folder]

Settings (.env, all optional):
  WEB_BROWSER=chrome     chrome | msedge | chromium   (chrome/msedge = the browser already installed on the PC, no extra download)
  WEB_HEADLESS=1         1 = no window, 0 = show the window (use 0 if Meesho blocks the hidden browser)
Needs once:  pip install playwright        (and only if you use WEB_BROWSER=chromium:  playwright install chromium)
"""
import io, os, re, sys, time

from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "browser_profile")          # own browser profile, so your normal Chrome is not touched
HEADLESS = os.getenv("WEB_HEADLESS", "1").strip().lower() not in ("0", "false", "no", "off")
BROWSER = os.getenv("WEB_BROWSER", "chrome").strip().lower()
MAX_IMAGES = 6
IMG_PATH = re.compile(r"/images/products/(\d+)/([^/_.?]+)")      # .../images/products/<product id>/<picture id>_512.webp?width=64
BLOCKED = re.compile(r"access denied|captcha|just a moment|are you a robot|unusual traffic|request blocked|verify you are human", re.I)
# the product text ends where other products start
_END = re.compile(r"^(similar products|products for you|you may also like|customers also viewed|more from|recommended for you|sponsored)", re.I)

JS = """() => {
  const abs = u => { try { return new URL(u, location.href).href; } catch (e) { return ''; } };
  const imgs = [];
  document.querySelectorAll('img').forEach(im => {
    [im.currentSrc, im.src].concat((im.srcset || '').split(',').map(x => x.trim().split(' ')[0])).forEach(u => { if (u) imgs.push(abs(u)); });
  });
  const meta = n => { const m = document.querySelector('meta[property="' + n + '"],meta[name="' + n + '"]'); return m ? (m.content || '') : ''; };
  const og = abs(meta('og:image'));
  if (og) imgs.unshift(og);
  const h1 = document.querySelector('h1');
  return { imgs: imgs, title: (h1 ? h1.innerText : '') || meta('og:title'), desc: meta('og:description') || meta('description'),
           text: document.body ? document.body.innerText : '', pageTitle: document.title, url: location.href };
}"""


def _launch(pw):
    """The browser with its own profile folder. Tries the installed Chrome / Edge first, then Playwright's own Chromium."""
    order = [BROWSER] + [b for b in ("chrome", "msedge", "chromium") if b != BROWSER]
    last = None
    for b in order:
        try:
            kw = dict(user_data_dir=PROFILE, headless=HEADLESS, viewport={"width": 1280, "height": 1600}, locale="en-IN",
                      args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
            if b != "chromium":
                kw["channel"] = b
            return pw.chromium.launch_persistent_context(**kw)
        except Exception as e:
            last = e
    raise RuntimeError("no browser could be started (%s). Install Google Chrome, or run: playwright install chromium" % str(last)[:200])


def _pick_images(urls):
    """Keep only the main product's pictures (the folder of the first picture), one file per picture id, original order.
    Returns [(picture_id, [candidate urls, best first])]."""
    folder, seen, order, cands = None, set(), [], {}
    for u in urls:
        m = IMG_PATH.search(u)
        if not m:
            continue
        if folder is None:
            folder = m.group(1)
        if m.group(1) != folder:                  # a picture of ANOTHER product (similar products, recommendations)
            continue
        pid = m.group(2)
        if pid not in seen:
            seen.add(pid)
            order.append(pid)
        cands.setdefault(pid, []).append(u)

    def rank(u):                                  # webp/jpg/png decode everywhere; avif only if nothing else
        ext = re.search(r"\.(\w+)(?:\?|$)", u)
        return 1 if ext and ext.group(1).lower() == "avif" else 0
    out = []
    for pid in order:
        c = sorted(set(cands[pid]), key=rank)
        wide = []                                 # the same file asked for a bigger size first
        for u in c:
            if re.search(r"[?&]width=\d+", u):
                wide.append(re.sub(r"([?&]width=)\d+", r"\g<1>720", u))
        out.append((pid, wide + c))
    return out


def _download(ctx, urls):
    last = ""
    for u in urls:
        try:
            r = ctx.request.get(u, timeout=20000)
            if not r.ok:
                last = "HTTP %s" % r.status
                continue
            im = Image.open(io.BytesIO(r.body()))
            im.load()
            if min(im.size) < 120:                 # an icon or a tiny thumbnail
                last = "too small %s" % (im.size,)
                continue
            return im.convert("RGB")
        except Exception as e:
            last = str(e)[:80]
    raise RuntimeError(last or "no url")


def _product_lines(text, title):
    """Page text from the product title down to where other products start (navigation junk above is dropped)."""
    lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    t = re.sub(r"\s+", " ", title or "").strip().lower()[:30]
    start = next((i for i, l in enumerate(lines) if t and l.lower().startswith(t)), 0)
    lines = lines[start:]
    for i, l in enumerate(lines):
        if i > 3 and _END.match(l):
            lines = lines[:i]
            break
    return lines[:300]


def fetch(url, out_dir):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright is not installed. Run once:  pip install playwright")
    os.makedirs(out_dir, exist_ok=True)
    with sync_playwright() as pw:
        ctx = _launch(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            print("  browser: %s, %s" % (BROWSER, "hidden" if HEADLESS else "visible"))
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("h1", timeout=20000)
            except Exception:
                pass
            for _ in range(5):                          # scroll a little so lazy pictures and the details load
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(700)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            data = page.evaluate(JS)
            try:
                with open(os.path.join(out_dir, "web_page.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())            # for debugging: send this file if the product is read wrongly
            except Exception:
                pass
            picks = _pick_images(data.get("imgs") or [])
            head = (data.get("pageTitle") or "") + " " + (data.get("text") or "")[:300]
            if not picks:
                if BLOCKED.search(head):
                    raise RuntimeError("Meesho blocked the browser (%s). Try WEB_HEADLESS=0 in .env" % head.strip()[:80])
                raise RuntimeError("no product pictures found on the page (is the link a product page? title: %s)" % (data.get("pageTitle") or "")[:80])
            carousel = []
            for n, (pid, urls) in enumerate(picks[:MAX_IMAGES]):
                try:
                    im = _download(ctx, urls)
                except Exception as e:
                    print("  picture %s skipped: %s" % (pid, e))
                    continue
                path = os.path.join(out_dir, "product.png" if not carousel else "image_%d.png" % (len(carousel) + 1))
                im.save(path)
                buf = io.BytesIO()
                im.save(buf, "PNG")
                carousel.append((path, buf.getvalue()))
            if not carousel:
                raise RuntimeError("the product pictures could not be downloaded")
            title = (data.get("title") or "").strip()
            texts = _product_lines(data.get("text"), title)
            if data.get("desc") and data["desc"] not in texts:
                texts.append(data["desc"])
            if title and not (texts and texts[0].lower().startswith(title.lower()[:30])):
                texts.insert(0, title)
            with open(os.path.join(out_dir, "web_page_text.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(texts))
            print("  web page read: %d product pictures, %d text lines" % (len(carousel), len(texts)))
            return carousel, [], texts
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print('Use: python meesho_web.py "PRODUCT_LINK" [output_folder]')
        sys.exit(0)
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "outputs_test", "web_" + time.strftime("%Y%m%d_%H%M%S"))
    c, _, t = fetch(sys.argv[1], out)
    print("\nFOLDER:", out)
    for p, png in c:
        im = Image.open(io.BytesIO(png))
        print("  picture:", p, im.size)
    print("TEXT (first 40 lines):")
    for l in t[:40]:
        print("  |", l[:140])
