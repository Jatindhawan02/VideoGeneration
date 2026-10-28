"""Build and verify a product-faithful Instagram Reel cover on the API/PC side."""
import io, json, os, re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import llm

W, H = 1080, 1920
BASE = Path(__file__).resolve().parent


def font(size, bold=True):
    candidates = ([BASE/'assets/fonts/DejaVuSans-Bold.ttf', Path('C:/Windows/Fonts/arialbd.ttf')] if bold else
                  [BASE/'assets/fonts/DejaVuSans.ttf', Path('C:/Windows/Fonts/arial.ttf')])
    for p in candidates:
        try:
            if p.exists(): return ImageFont.truetype(str(p), size)
        except Exception: pass
    return ImageFont.load_default()


def clean_text(value, limit=45):
    s = re.sub(r'\s+', ' ', str(value or '')).strip()
    s = re.sub(r'[₹$€£]\s?\d[\d,.]*|\b(?:rs\.?|inr)\s?\d[\d,.]*', '', s, flags=re.I)
    s = re.sub(r'\b(?:best|guaranteed|discount|sale|cheap|premium|price|must[- ]?have)\b', '', s, flags=re.I)
    return re.sub(r'\s+', ' ', s).strip(' -:')[:limit]


def _fields(product):
    name = str(product.get('name') or product.get('title') or 'Useful Find')[:140]
    facts = str(product.get('features') or product.get('specifications') or product.get('page_text') or product.get('description') or '')[:3000]
    prompt = ('Create concise English JSON for a product cover using only verified facts. Product: '+name+'\nFacts: '+facts+
      '\nReturn {"headline":"2-5 words","tagline":"short phrase","points":["...","...","...","..."],"callout":"short line","bottom_labels":["...","...","...","..."]}. '
      'No price, currency, discount, best, guaranteed, premium, must-have, or invented claims. If facts are sparse, use neutral wording. Points <=24 chars, labels <=24 chars.')
    def validate(raw):
        try:
            d=json.loads(raw)
            if not all(isinstance(d.get(k),str) for k in ('headline','tagline','callout')): return 'missing text fields'
            if len(d.get('points',[])) != 4 or len(d.get('bottom_labels',[])) != 4: return 'needs 4 points and 4 labels'
            return None
        except Exception: return 'invalid JSON'
    try: d=json.loads(llm.generate(prompt, json_mode=True, rounds=2, validate=validate))
    except Exception:
        d={'headline':name[:28] or 'Useful Find','tagline':'A closer look at this find','points':['Simple design','Everyday use','Easy to style','Thoughtful detail'],'callout':'Take a closer look','bottom_labels':['Design','Details','Everyday use','Gift idea']}
    d['headline']=clean_text(d['headline'],38) or 'Useful Find'
    d['tagline']=clean_text(d['tagline'],64) or 'A closer look at this find'
    d['points']=[clean_text(x,24) or 'Product detail' for x in d['points'][:4]]
    d['callout']=clean_text(d['callout'],42) or 'Take a closer look'
    d['bottom_labels']=[clean_text(x,24) or ['Design','Details','Everyday use','Gift idea'][i] for i,x in enumerate(d['bottom_labels'][:4])]
    return d,prompt


def _background(product):
    category=str(product.get('category') or product.get('name') or product.get('title') or 'product')[:100]
    prompt=('Create a soft pastel pink, cream and peach scrapbook-paper background for a vertical 9:16 social-commerce cover about '+category+'. '
      'Background only: no product, people, letters, text, logos or icons. Subtle paper texture, gentle warm daylight, uncluttered open centre.')
    try:
        raw=llm.generate_image(prompt)
        im=Image.open(io.BytesIO(raw)).convert('RGB').resize((W,H),Image.Resampling.LANCZOS)
        source='Gemini image model'
    except Exception as e:
        print('  [cover] background fallback:',str(e)[:110])
        im=Image.new('RGB',(W,H)); pix=im.load(); top=(255,239,232); bot=(250,214,226)
        for y in range(H):
            t=y/(H-1); c=tuple(int(top[i]*(1-t)+bot[i]*t) for i in range(3))
            for x in range(W): pix[x,y]=c
        source='Pillow pastel gradient (image API unavailable)'
    return im,prompt,source


def _wrap(draw,text,f,maxw):
    lines=[]; cur=''
    for word in str(text).split():
        test=(cur+' '+word).strip()
        if draw.textbbox((0,0),test,font=f)[2] <= maxw: cur=test
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines


def _fit(draw,text,maxw,size,minsize=17,bold=True):
    for n in range(size,minsize-1,-2):
        f=font(n,bold)
        if draw.textbbox((0,0),text,font=f)[2] <= maxw: return f
    return font(minsize,bold)


def build_cover(product_path, product, run_dir):
    run_dir=Path(run_dir); run_dir.mkdir(parents=True,exist_ok=True)
    fields, fields_prompt=_fields(product)
    bg,bg_prompt,source=_background(product)
    canvas=bg.convert('RGBA'); d=ImageDraw.Draw(canvas,'RGBA')
    d.rounded_rectangle((24,24,W-24,H-24),radius=38,outline=(255,255,255,230),width=8)
    d.rounded_rectangle((55,58,1025,340),radius=42,fill=(255,250,237,247),outline=(255,255,255,255),width=5)
    f=_fit(d,fields['headline'],880,82,40); d.text((90,90),fields['headline'],font=f,fill=(83,47,61,255))
    tf=_fit(d,fields['tagline'],880,36,24,False); d.text((92,225),fields['tagline'],font=tf,fill=(105,70,81,255))
    # Product photo is kept intact; only resized to fit its display card.
    product_im=Image.open(product_path).convert('RGBA'); product_im.thumbnail((600,900),Image.Resampling.LANCZOS)
    card=Image.new('RGBA',(650,970),(255,255,255,0)); cd=ImageDraw.Draw(card)
    cd.rounded_rectangle((0,0,649,969),radius=34,fill=(255,255,255,242),outline=(255,255,255,255),width=5)
    card.alpha_composite(product_im,((650-product_im.width)//2,(970-product_im.height)//2))
    canvas.alpha_composite(card,(45,405)); d=ImageDraw.Draw(canvas,'RGBA')
    d.rounded_rectangle((730,380,1010,780),radius=38,fill=(255,185,207,249),outline=(255,255,255,255),width=5)
    d.text((760,410),'THE DETAILS',font=font(29),fill=(98,44,66,255)); yy=465
    for i,txt in enumerate(fields['points']):
        d.ellipse((755,yy+4,790,yy+39),fill=(255,250,238,255),outline=(156,74,100,255),width=2)
        d.text((764,yy+4),str(i+1),font=font(22),fill=(117,45,72,255))
        ff=_fit(d,txt,190,25,17,False)
        for j,line in enumerate(_wrap(d,txt,ff,190)[:2]): d.text((800,yy+j*29),line,font=ff,fill=(83,47,61,255))
        yy+=77
    for i,(label,sub) in enumerate([('A closer look','Product details'),('Made for you','Everyday finds')]):
        y=820+i*190; d.rounded_rectangle((735,y,1010,y+155),radius=24,fill=(255,250,243,242),outline=(255,255,255,255),width=4)
        d.ellipse((770,y+20,835,y+85),fill=(255,214,225,255)); d.text((787,y+30),'*',font=font(32),fill=(120,62,82,255))
        d.text((850,y+26),label,font=_fit(d,label,145,22,16)); d.text((850,y+64),sub,font=_fit(d,sub,145,18,14,False),fill=(100,72,80,255))
    d.rounded_rectangle((670,1210,1020,1395),radius=30,fill=(255,183,205,250),outline=(255,255,255,255),width=5)
    yy=1240; cf=font(27)
    for line in _wrap(d,fields['callout'],cf,300)[:3]: d.text((700,yy),line,font=cf,fill=(91,43,63,255)); yy+=36
    d.line([(700,1205),(625,1145),(585,1080)],fill=(224,91,133,255),width=12)
    d.line([(585,1080),(588,1128)],fill=(224,91,133,255),width=12); d.line([(585,1080),(630,1097)],fill=(224,91,133,255),width=12)
    d.rounded_rectangle((42,1580,1038,1848),radius=36,fill=(255,249,232,250),outline=(255,255,255,255),width=5)
    icons=['♡','◇','▣','✦']; colw=242
    for i,label in enumerate(fields['bottom_labels']):
        cx=42+i*colw+colw//2; d.ellipse((cx-42,1605,cx+42,1689),fill=(255,215,226,255),outline=(255,255,255,255),width=3)
        d.text((cx-22,1613),icons[i],font=font(43),fill=(111,50,74,255))
        ff=_fit(d,label,colw-22,22,15,False)
        for j,line in enumerate(_wrap(d,label,ff,colw-22)[:2]):
            bb=d.textbbox((0,0),line,font=ff); d.text((cx-(bb[2]-bb[0])/2,1700+j*27),line,font=ff,fill=(83,47,61,255))
    out=run_dir/'cover.png'; canvas.convert('RGB').save(out,'PNG',optimize=True)
    (run_dir/'cover_prompt.txt').write_text('BACKGROUND PROMPT\n'+bg_prompt+'\n\nTEXT-FIELDS PROMPT\n'+fields_prompt+'\n\nFIELDS JSON\n'+json.dumps(fields,ensure_ascii=False,indent=2)+'\n\nBACKGROUND SOURCE\n'+source+'\n',encoding='utf-8')
    return str(out),fields,source


def verify_cover(cover_path, product_path, fields):
    im=Image.open(cover_path).convert('RGB')
    if im.width < 600 or im.height < 1000 or abs(im.width/im.height-9/16) > .04: return False,'cover is not 9:16'
    if im.getbbox() is None or im.getextrema()==((0,0),(0,0),(0,0)): return False,'cover appears blank'
    try:
        from google.genai import types
        cp=types.Part.from_bytes(data=Path(cover_path).read_bytes(),mime_type='image/png')
        mime='image/jpeg' if Path(product_path).suffix.lower() in ('.jpg','.jpeg') else 'image/png'
        pp=types.Part.from_bytes(data=Path(product_path).read_bytes(),mime_type=mime)
        prompt=('Compare image 1 (cover) with image 2 (original product photo). Return JSON with boolean keys same_product, readable, no_price_or_claims, safe_margins, plus reason. '
          'Verify product fidelity, readable text, no price/discount/best/guaranteed claims, and no important edge clipping. Expected text: '+json.dumps(fields,ensure_ascii=False))
        def valid(raw):
            try:
                x=json.loads(raw); return None if all(isinstance(x.get(k),bool) for k in ('same_product','readable','no_price_or_claims','safe_margins')) else 'missing boolean fields'
            except Exception: return 'invalid JSON'
        verdict=json.loads(llm.generate([cp,pp,prompt],json_mode=True,rounds=2,validate=valid))
        ok=all(verdict.get(k) is True for k in ('same_product','readable','no_price_or_claims','safe_margins'))
        return ok,verdict.get('reason','vision check passed' if ok else 'vision check failed')
    except Exception as e: return False,'vision verification unavailable: '+str(e)[:100]


def create_verified_cover(product_path, product, run_dir, attempts=3):
    last='not attempted'
    for n in range(1,attempts+1):
        try:
            path,fields,_=build_cover(product_path,product,run_dir)
            ok,last=verify_cover(path,product_path,fields)
            print('  [cover] verification %d/%d: %s - %s'%(n,attempts,'OK' if ok else 'FAIL',last))
            if ok: return path,fields
        except Exception as e: last=str(e)[:160]
    print('  [cover] verification failed after %d attempts: %s'%(attempts,last))
    return None,None
