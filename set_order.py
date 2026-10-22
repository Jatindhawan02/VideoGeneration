import re
ORDER = "gemini,groq,nvidia,cloudflare,zai,openrouter,mistral"
lines = open(".env", encoding="utf-8-sig").read().splitlines()
found = False
for i, l in enumerate(lines):
    if re.match(r"\s*PROVIDER_ORDER\s*=", l):
        lines[i] = "PROVIDER_ORDER=" + ORDER
        found = True
if not found:
    lines.append("PROVIDER_ORDER=" + ORDER)
open(".env", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print("Done: PROVIDER_ORDER =", ORDER)
