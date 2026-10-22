src = open("script_writer.py", encoding="utf-8-sig").read()
OLD = r"""    look = "Vertical 9:16, 10 seconds, bright clean background, no text on screen, no people.\""""
OLD = 'look = "Vertical 9:16, 10 seconds, bright clean background, no text on screen, no people."'
NEW = 'look = "9:16, bright clean background, no text on screen, no people."'
if src.count(OLD) != 1:
    raise SystemExit("look wali line nahi mili, kuch nahi badla")
open("script_writer.py.bak7", "w", encoding="utf-8").write(src)
open("script_writer.py", "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("script_writer.py patched")
