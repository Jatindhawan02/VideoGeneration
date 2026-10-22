import json
import llm

def make_caption(product, info):
    prompt = f"""Write an Instagram Reel caption for this product video.
Product: {product['name']} - {product['description']}
Video summary: {info.get('summary','')}
Voiceover: {info.get('transcript','')}
Rules: catchy first line, 2-3 short lines, no price or 'guaranteed/best' claims,
end with 'Comment LINK to get the link in your DM', then 6 relevant hashtags.
Return JSON: {{"caption": "..."}}"""
    return json.loads(llm.generate([prompt]))["caption"]
