import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

FONT = Path(__file__).resolve().parents[1] / 'assets/fonts/Baloo2.ttf'


def neutral_cover(topic):
    value = (topic or '').lower()
    if any(w in value for w in ('pique', 'पिके', 'पीके')):
        return 'POLO की बनावट | फाइबर या बुनाई?', 'POLO TEXTURE | FIBRE OR KNIT?'
    if 'dtf' in value or 'डीटीएफ' in value:
        return ('DTF PRINT | नमी से क्या बदलता है?', 'DTF PRINT | NAMI SE KYA BADALTA HAI?') if any(w in value for w in ('humid', 'rain', 'monsoon', 'नमी', 'बारिश')) else ('DTF PRINT | क्या जाँचें?', 'DTF PRINT | KYA CHECK KAREIN?')
    if any(w in value for w in ('oversized', 'regular fit', 'साइज़', 'size', 'boxy')):
        return 'टी-शर्ट फिट | कैसे चुनें?', 'T-SHIRT FIT | KAISE CHUNEIN?'
    if any(w in value for w in ('collar', 'fusing', 'कॉलर')):
        return 'POLO COLLAR | क्या जाँचें?', 'POLO COLLAR | KYA CHECK KAREIN?'
    if any(w in value for w in ('rub', 'dye', 'bleed', 'colourfast')):
        return 'COLOUR BLEED | क्यों होता है?', 'COLOUR BLEED | KYUN HOTA HAI?'
    if any(w in value for w in ('sublimation', 'yarn', 'polyester')):
        return 'POLYESTER | क्या अलग है?', 'POLYESTER | KYA ALAG HAI?'
    if any(w in value for w in ('moq', 'minimum', 'bulk order')):
        return 'BULK से पहले | क्या जाँचें?', 'BEFORE BULK | KYA CHECK KAREIN?'
    if any(w in value for w in ('biowash', 'bio-wash')):
        return 'BIOWASH | असल में क्या है?', 'BIOWASH | ASAL MEIN KYA HAI?'
    if any(w in value for w in ('gsm', 'cotton', 'fabric')):
        return 'टी-शर्ट का कपड़ा | क्या जाँचें?', 'T-SHIRT FABRIC | KYA CHECK KAREIN?'
    if any(w in value for w in ('print', 'प्रिंट')):
        return 'प्रिंट से पहले | क्या जाँचें?', 'BEFORE PRINTING | KYA CHECK KAREIN?'
    return 'टी-शर्ट खरीदने से पहले | क्या जाँचें?', 'BEFORE BUYING T-SHIRTS | KYA CHECK KAREIN?'


def validate_cover_text(text, script, rates=()):
    """rates: exact live website rates cited by the lesson (tools/current_rates.allowed_amounts)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Cover wording is empty')
    lines = [line.strip() for line in text.split('|')]
    if len(lines) != 2 or not all(lines):
        raise ValueError('Cover needs two complete short lines')
    if not 3 <= len(' '.join(lines).split()) <= 8 or len(text) > 90:
        raise ValueError('Rewrite the full cover within 3–8 words; never truncate')
    if re.search(r'\b(?:lakhs?|crores?|lost|loss(?:es)?|returns?|returned|destroyed|rejected|rejections?)\b|नुकसान|लाख|बर्बाद', text, re.I):
        raise ValueError('Cover cannot invent or amplify monetary loss or batch-return claims')
    normalized = text.translate(str.maketrans('०१२३४५६७८९', '0123456789'))
    normalized_script = (script or '').translate(str.maketrans('०१२३४५६७८९', '0123456789'))
    priced = set()
    for match in re.finditer(r'(?:₹|\brs\.?)\s*(\d+)', normalized, re.I):
        amount = int(match.group(1))
        spoken = re.search(r'(?:₹|\brs\.?)\s*' + str(amount) + r'(?!\d)', normalized_script, re.I)
        if amount not in rates or not spoken:
            raise ValueError('A cover price must be an exact live website rate already spoken in the script')
        priced.add(match.start(1))
    if re.search(r'₹(?!\s*\d)|\brs\.?(?!\s*\d)', normalized, re.I):
        raise ValueError('A cover price must be an exact live website rate already spoken in the script')
    for match in re.finditer(r'\d+(?:\.\d+)?', normalized):
        if match.start() in priced:
            continue
        # A GSM, DPI, pixel or export-size value (DTF lessons, 3-Oct-2026) only when the script says exactly that value.
        claim = re.match(r'\d+(?:\.\d+)?\s*(?:GSM|DPI|px)\b|\d+(?:\.\d+)?x\b', normalized[match.start():], re.I)
        if not claim or not re.search(r'(?<!\d)' + re.escape(claim.group()).replace(r'\ ', r'\s*') + r'(?!\w)', normalized_script, re.I):
            raise ValueError('Only an exact script-grounded GSM, DPI, px or export-size value is allowed; omit prices, counts and outcomes')
    if not lines[-1].endswith('?'):
        raise ValueError('Cover must be the buyer\'s complete question ending with ? (owner Option B)')
    for line in lines:
        if re.search(r'(?:^|\s)(?:बिना|में|का|की|के|से|और|या|vs|with|without|of|the|and|or)\s*[?!।.]*$', line, re.I):
            raise ValueError('Cover ends on an unfinished phrase')
    return lines


def choose_cover(text, latin, script, topic, rates=()):
    fallback = neutral_cover(topic)
    try:
        validate_cover_text(text, script, rates)
        validate_cover_text(latin, script, rates)
        if re.search(r'[\u0900-\u097f]', latin):
            raise ValueError('Latin wording still contains Devanagari')
        return text, latin, 'validated'
    except ValueError as exc:
        validate_cover_text(fallback[0], script)
        validate_cover_text(fallback[1], script)
        return *fallback, 'neutral-fallback: ' + str(exc)


def render_youtube_cover(scene, lines, path):
    from PIL import ImageFont, features
    if any(re.search(r'[\u0900-\u097f]', line) for line in lines) and not features.check('raqm'):
        raise ValueError('Hindi cover needs RAQM shaping')
    image = ImageOps.fit(scene.convert('RGB'), (1280, 720), method=Image.Resampling.LANCZOS)
    image = ImageEnhance.Brightness(image).enhance(0.62)
    draw = ImageDraw.Draw(image)
    boxes = []
    y = 175
    for index, line in enumerate(lines):
        size = 108
        while size >= 34:
            font = ImageFont.truetype(str(FONT), size)
            try:
                font.set_variation_by_axes([800])
            except OSError:
                pass
            box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
            if box[2] - box[0] <= 544:
                break
            size -= 2
        else:
            raise ValueError('Cover text cannot fit readable centre crop')
        height = box[3] - box[1]
        x = (1280 - (box[2] - box[0])) // 2 - box[0]
        draw.text((x, y - box[1]), line, font=font,
                  fill='#FFFFFF' if index == 0 else '#FFD400', stroke_width=3, stroke_fill='#07131d')
        boxes.append([x + box[0], y, x + box[2], y + height])
        y += height + 26
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path, 'PNG', optimize=True)
    return {'width': 1280, 'height': 720, 'text_boxes': boxes,
            'central_4_5_window': [352, 0, 928, 720], 'single_text_layer': True}


def prepend_cover(video_path, cover_path):
    import json
    import os
    import subprocess
    import tempfile
    video_path = Path(video_path)
    raw = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video_path)], check=True, capture_output=True, text=True)
    probe = json.loads(raw.stdout)
    stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    if not any(s['codec_type'] == 'audio' for s in probe['streams']):
        raise ValueError('Daily video has no audio')
    seconds = float(probe['format']['duration'])
    if not 1 <= seconds <= 180:
        raise ValueError('Unexpected daily Short duration')
    w, h = stream['width'], stream['height']
    filters = (f'[0:v]scale={w}:{h},setsar=1,fps=30,format=yuv420p,trim=duration=0.5,setpts=PTS-STARTPTS[c];'
               '[1:v]setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[b];'
               '[c][b]concat=n=2:v=1:a=0[v];'
               f'[1:a]adelay=500:all=1,apad=pad_dur=0.1,atrim=duration={seconds + 0.5}[a]')
    with tempfile.TemporaryDirectory(prefix='daily-cover-', dir=video_path.parent) as temp:
        result = Path(temp) / 'covered.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-framerate', '30', '-t', '0.5', '-i', str(cover_path), '-i', str(video_path), '-filter_complex', filters, '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(result)], check=True, capture_output=True)
        actual = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(result)], check=True, capture_output=True, text=True).stdout)
        if abs(actual - seconds - 0.5) > 0.1:
            raise ValueError('Cover render changed the body duration')
        os.replace(result, video_path)
    return {'cover_seconds': 0.5, 'before_seconds': seconds, 'after_seconds': actual,
            'audio_processing': '500ms delay only; AAC re-encode; no denoising', 'body_complete': True}
