"""Try ONLY the phone part of the cover step. It never shares anything.

1. On the phone open Instagram, pick a video and go Next until the final 'New reel' screen (caption box + Share) is visible.
2. python test_cover_phone.py path/to/cover.png            -> sets that picture as the cover
   python test_cover_phone.py --default                    -> only the 'no black cover' routine (slides the frame selector)
"""
import sys
import phone, post_guard

d = phone.connect()
phone.open_app(d, "com.instagram.android")
if "--default" in sys.argv:
    print(post_guard._cover_default(d, force=True))
else:
    post_guard.COVER.update(path=sys.argv[1], avoid_black=True)
    print("final screen visible:", post_guard._composer_visible(d))
    print("RESULT:", post_guard._cover_set(d))
    if not post_guard._composer_visible(d):
        post_guard._back_to_composer(d)
