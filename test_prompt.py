import sys
sys.stdout.reconfigure(encoding="utf-8")
import script_writer as s
body = "Partner से बिना लड़े गुस्सा दिखाओ। ये reversible octopus flip करके pink से blue face निकालो।"
scenes = "Close-up of the happy pink octopus toy sitting on a bed beside a laptop.; The toy is quickly inverted by hands to reveal the frowning blue face."
facts = "Double-sided reversible design; Happy pink and angry blue expressions"
p = s._build_prompt("Printustaad Reversible Octopus Plush Toy", body, scenes, facts=facts)
print(len(p), "characters\n")
print(p)
