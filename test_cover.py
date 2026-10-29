"""Try the cover on an old run folder WITHOUT the phone.

    python test_cover.py outputs_test/20261027_113323 --name "Water Bottle" --desc "Pink sipper bottle with straw"
    python test_cover.py outputs_test/20261027_113323 --name "Water Bottle" --gradient     (no image API, plain gradient background)

Needs reference.png in the folder (reference_2/3.png are used for the small tiles). Writes cover.png, cover_prompt.txt there.
"""
import argparse, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--name", required=True)
ap.add_argument("--desc", default="")
ap.add_argument("--visible", default="")
ap.add_argument("--gradient", action="store_true", help="skip the image API")
a = ap.parse_args()
if a.gradient:
    os.environ["COVER_NO_IMAGE_API"] = "1"
import cover_maker
p = {"name": a.name, "description": a.desc, "visible": a.visible}
res = cover_maker.build_cover(a.folder, p, None)
print("\nRESULT:", res)
if res.get("path"):
    try:
        os.startfile(res["path"])
    except Exception:
        pass
sys.exit(0 if res.get("ok") else 1)
