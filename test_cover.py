"""Build + verify a Reel cover from an existing output run folder, without connecting to a phone.
Usage: python test_cover.py outputs_test/20261027_120258
"""
import os, sys
from pathlib import Path
import cover_maker

if len(sys.argv) < 2:
    raise SystemExit('Usage: python test_cover.py <run-folder> [product.json]')
run_dir=Path(sys.argv[1]).resolve()
product_path=next((run_dir/n for n in ('reference.png','product.png') if (run_dir/n).is_file()),None)
if not product_path:
    raise SystemExit('No reference.png or product.png found in '+str(run_dir))
product={}
if len(sys.argv)>2:
    import json
    product=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
else:
    # Best-effort product facts from files commonly saved by this project.
    for name in ('product.json','product_facts.json','research.json'):
        f=run_dir/name
        if f.is_file():
            try: product=json.loads(f.read_text(encoding='utf-8')); break
            except Exception: pass
    product.setdefault('name',run_dir.name.replace('_',' '))
path,fields,source=cover_maker.build_cover(str(product_path),product,str(run_dir))
ok,why=cover_maker.verify_cover(path,str(product_path),fields)
print('Cover:',path)
print('Background:',source)
print('Verification:', 'PASS' if ok else 'FAIL', '-', why)
if not ok: raise SystemExit(2)
