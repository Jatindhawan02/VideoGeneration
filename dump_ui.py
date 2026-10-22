"""Run this while a Meesho PRODUCT PAGE is open on the phone. It prints the lines about wishlist/heart buttons."""
import os, re
import phone

os.makedirs("logs", exist_ok=True)
d = phone.connect()
try:
    xml = d.page_source
    with open("logs/ui_dump.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    with open("logs/ui_dump.png", "wb") as f:
        f.write(phone.screenshot(d))
    print("screen size:", d.get_window_size())
    print("---- lines about wishlist / heart ----")
    found = 0
    for m in re.finditer(r"<[^>]+>", xml):
        tag = m.group(0)
        if re.search(r"wish|heart|favou?rite|like", tag, re.I):
            found += 1
            print(tag[:500])
    if not found:
        print("(nothing found with these words)")
    print("---- end ----")
finally:
    d.quit()
