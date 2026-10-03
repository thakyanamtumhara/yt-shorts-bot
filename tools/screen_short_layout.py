"""Text layers for real-screen Shorts (DTF launch, owner 3-Oct-2026: the captions written over the website text did not
look good, and the hook box, the MOQ end card and a looping recording failed the owner review of run 336).

The website recording sits low on a dark backdrop. Every text layer here lives in the band above it, on the site's own
header bar, or on its own end card: never over the page content. Drawn with PIL (no ImageMagick), so they can be
rendered and checked anywhere.
"""
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

ROOT = Path(__file__).resolve().parents[1]
LATIN_FONTS = (
    '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
    '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
)
DEVANAGARI_FONT = ROOT / 'assets/fonts/Baloo2.ttf'
YELLOW, WHITE, BLACK = (255, 215, 0, 255), (255, 255, 255, 255), (0, 0, 0, 255)
CTA_RED = (230, 60, 20)
OUTRO_BG = (15, 15, 25)

# The band above the recording: platform top icons above HOOK_TOP, the Sale91.com watermark badge below HOOK_BOTTOM.
HOOK_TOP, HOOK_BOTTOM = 112, 316


# The DTF site's sticky header is 171 px tall in the 1080x1920 recordings (assets/dtf_demo).
SITE_HEADER_PX = 171


def screen_top(video_height=1920, scale=0.76, bottom=40):
    """y of the recording's top edge (the site's sticky header starts here)."""
    return video_height - int(video_height * scale) - bottom


def header_box(video_width=1080, video_height=1920, scale=0.76, bottom=40):
    """(x, y, width, height) of the site's header bar inside the composed frame, 1 px wider on each side."""
    width = int(video_width * scale)
    return (video_width - width) // 2 - 1, screen_top(video_height, scale, bottom), width + 2, round(SITE_HEADER_PX * scale)


def _font(size, text=''):
    if re.search(r'[ऀ-ॿ]', text) and DEVANAGARI_FONT.exists():
        engine = ImageFont.Layout.RAQM if features.check('raqm') else ImageFont.Layout.BASIC
        font = ImageFont.truetype(str(DEVANAGARI_FONT), size, layout_engine=engine)
        try:
            font.set_variation_by_axes([800])
        except OSError:
            pass
        return font
    for path in LATIN_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError('No bold font for the screen Short text')


def _lines(draw, words, font, max_width, stroke):
    lines, line = [], []
    for word in words:
        trial = line + [word]
        box = draw.textbbox((0, 0), ' '.join(trial), font=font, stroke_width=stroke)
        if line and box[2] - box[0] > max_width:
            lines.append(line)
            line = [word]
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def hook_band_image(text, width=1080, max_height=HOOK_BOTTOM - HOOK_TOP, max_words=6):
    """The scroll-stop hook for the band above the recording: first word yellow, the rest white, black outline.
    Returns an RGBA image no taller than max_height, or raises ValueError when the words cannot fit readably."""
    words = (text or '').split()[:max_words]
    if not words:
        raise ValueError('Empty hook')
    stroke = 5
    probe = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    for size in range(88, 43, -4):
        font = _font(size, text)
        lines = _lines(probe, words, font, width - 160, stroke)
        if len(lines) > 3:
            continue
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + stroke * 2
        gap = int(size * 0.12)
        height = line_h * len(lines) + gap * (len(lines) - 1)
        if height <= max_height:
            break
    else:
        raise ValueError('Hook does not fit the band readably')
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    space = draw.textlength(' ', font=font)
    first = True
    for row, line in enumerate(lines):
        widths = [draw.textlength(word, font=font) for word in line]
        x = (width - (sum(widths) + space * (len(line) - 1))) / 2
        y = row * (line_h + gap) + stroke
        for word, word_w in zip(line, widths):
            draw.text((x, y), word, font=font, fill=YELLOW if first else WHITE, stroke_width=stroke, stroke_fill=BLACK)
            first = False
            x += word_w + space
    return image


def cta_strip_image(text, width=1080, height=72):
    """The launch line as a brand strip; on a real-screen Short it covers exactly the site's own header bar.
    'A - B' becomes two lines (A large, B smaller) when the strip is tall enough."""
    image = Image.new('RGBA', (width, height), CTA_RED + (255,))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 2), fill=(255, 255, 255, 128))
    parts = [p.strip() for p in text.split(' - ', 1)] if ' - ' in text and height >= 110 else [text]
    starts = (48, 40) if len(parts) == 2 else (38,)
    fitted = []
    for part, start in zip(parts, starts):
        for size in range(start, 21, -2):
            font = _font(size, part)
            box = draw.textbbox((0, 0), part, font=font)
            if box[2] - box[0] <= width - 60:
                break
        else:
            raise ValueError('CTA text does not fit the strip')
        fitted.append((part, font, box))
    gap = 8
    total = sum(b[3] - b[1] for _, _, b in fitted) + gap * (len(fitted) - 1)
    if total > height - 12:
        raise ValueError('CTA text does not fit the strip')
    y = (height - total) / 2
    for index, (part, font, box) in enumerate(fitted):
        draw.text(((width - (box[2] - box[0])) / 2 - box[0], y - box[1]), part, font=font, fill=YELLOW if index else WHITE)
        y += box[3] - box[1] + gap
    return image


def outro_card_image(title, sub, cta, size=(1080, 1920)):
    """The 2-second end card for a campaign Short (the regular card says 'MOQ sirf 10 pieces', a T-shirt line)."""
    width, height = size
    image = Image.new('RGB', size, OUTRO_BG)
    draw = ImageDraw.Draw(image)
    for text, colour, start, centre in ((title, WHITE, 128, 0.40), (sub, YELLOW, 66, 0.51), (cta, WHITE, 52, 0.60)):
        if not text:
            continue
        for font_size in range(start, 23, -4):
            font = _font(font_size, text)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= width - 120:
                break
        else:
            raise ValueError('End card text does not fit')
        draw.text(((width - (box[2] - box[0])) / 2 - box[0], height * centre - (box[3] - box[1]) / 2 - box[1]), text,
                  font=font, fill=colour)
    return image
