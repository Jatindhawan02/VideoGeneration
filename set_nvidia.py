import re
MODEL = "meta/llama-3.2-11b-vision-instruct"
lines = open(".env", encoding="utf-8-sig").read().splitlines()
found = False
for i, l in enumerate(lines):
    if re.match(r"\s*NVIDIA_MODEL\s*=", l):
        lines[i] = "NVIDIA_MODEL=" + MODEL
        found = True
if not found:
    lines.append("NVIDIA_MODEL=" + MODEL)
open(".env", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print("Done: NVIDIA_MODEL =", MODEL)
