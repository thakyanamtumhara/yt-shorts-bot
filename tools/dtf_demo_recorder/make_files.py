import math, os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = os.path.join(os.environ.get('REEL_DIR', '/tmp/dtf-reel'), 'files')
BLACK = '/System/Library/Fonts/Supplemental/Arial Black.ttf'
ROUND = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
W, H = 6840, 11700

def font(path, size):
    return ImageFont.truetype(path, size)

def centered_text(d, cx, cy, text, f, fill, stroke=0, stroke_fill=None):
    b = d.textbbox((0, 0), text, font=f, stroke_width=stroke)
    w, h = b[2] - b[0], b[3] - b[1]
    d.text((cx - w / 2 - b[0], cy - h / 2 - b[1]), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)

def star(cx, cy, r1, r2, n=5, rot=-90):
    pts = []
    for i in range(n * 2):
        r = r1 if i % 2 == 0 else r2
        a = math.radians(rot + i * 180 / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

PAL = [
    ('#FF5A36', '#1E2A78', '#FFD23F'),
    ('#00A6A6', '#FFFFFF', '#1B1B3A'),
    ('#7B2CBF', '#FFD60A', '#FFFFFF'),
    ('#06D6A0', '#073B4C', '#FFFFFF'),
]

def design(kind, size, pal, label=None):
    """One design on its own transparent tile (size x size)."""
    a, b, c = pal
    s = size
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = s * 0.06
    if kind == 'badge':
        d.ellipse([m, m, s - m, s - m], fill=a)
        d.ellipse([m * 2.2, m * 2.2, s - m * 2.2, s - m * 2.2], outline=c, width=int(s * 0.025))
        centered_text(d, s / 2, s * 0.40, label or 'GOOD', font(BLACK, int(s * 0.17)), b)
        centered_text(d, s / 2, s * 0.60, 'VIBES', font(BLACK, int(s * 0.17)), c)
    elif kind == 'star':
        d.polygon(star(s / 2, s / 2, s * 0.47, s * 0.22), fill=c)
        d.polygon(star(s / 2, s / 2, s * 0.40, s * 0.18), fill=a)
        centered_text(d, s / 2, s * 0.53, label or 'DTF', font(BLACK, int(s * 0.15)), b)
    elif kind == 'banner':
        d.rounded_rectangle([m, s * 0.30, s - m, s * 0.70], radius=s * 0.08, fill=b)
        d.rounded_rectangle([m * 1.8, s * 0.36, s - m * 1.8, s * 0.64], radius=s * 0.06, outline=c, width=int(s * 0.018))
        centered_text(d, s / 2, s * 0.44, label or 'PRINT', font(BLACK, int(s * 0.12)), c)
        centered_text(d, s / 2, s * 0.56, 'READY', font(BLACK, int(s * 0.12)), a)
    elif kind == 'heart':
        r = s * 0.22
        d.ellipse([s * 0.5 - 2 * r, s * 0.18, s * 0.5, s * 0.18 + 2 * r], fill=a)
        d.ellipse([s * 0.5, s * 0.18, s * 0.5 + 2 * r, s * 0.18 + 2 * r], fill=a)
        d.polygon([(s * 0.5 - 2 * r + s * 0.012, s * 0.18 + r * 1.3), (s * 0.5 + 2 * r - s * 0.012, s * 0.18 + r * 1.3), (s * 0.5, s * 0.90)], fill=a)
        centered_text(d, s / 2, s * 0.45, label or 'LOVE', font(BLACK, int(s * 0.13)), b)
    elif kind == 'bolt':
        d.polygon([(s * 0.58, m), (s * 0.20, s * 0.56), (s * 0.46, s * 0.56), (s * 0.36, s - m), (s * 0.80, s * 0.40), (s * 0.54, s * 0.40), (s * 0.66, m)], fill=c)
        centered_text(d, s / 2, s * 0.88, label or 'POWER', font(BLACK, int(s * 0.11)), a, stroke=int(s * 0.012), stroke_fill=b)
    elif kind == 'jersey':
        centered_text(d, s / 2, s * 0.22, 'TEAM', font(BLACK, int(s * 0.14)), b, stroke=int(s * 0.012), stroke_fill=c)
        centered_text(d, s / 2, s * 0.60, label or '07', font(BLACK, int(s * 0.46)), a, stroke=int(s * 0.02), stroke_fill=b)
    elif kind == 'sun':
        for i in range(12):
            ang = math.radians(i * 30)
            x1, y1 = s / 2 + s * 0.30 * math.cos(ang), s / 2 + s * 0.30 * math.sin(ang)
            x2, y2 = s / 2 + s * 0.46 * math.cos(ang), s / 2 + s * 0.46 * math.sin(ang)
            d.line([(x1, y1), (x2, y2)], fill=c, width=int(s * 0.05))
        d.ellipse([s * 0.24, s * 0.24, s * 0.76, s * 0.76], fill=a)
        centered_text(d, s / 2, s * 0.50, label or 'SUN', font(BLACK, int(s * 0.12)), b)
    return im

def soften(tile, glow_color, radius):
    """Add a wide soft glow + drop shadow behind a design (many semi-transparent pixels)."""
    s = tile.size[0]
    pad = Image.new('RGBA', tile.size, (0, 0, 0, 0))
    alpha = tile.getchannel('A')
    glow_a = alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(radius)).point(lambda v: int(v * 0.85))
    glow = Image.new('RGBA', tile.size, glow_color + (0,))
    glow.putalpha(glow_a)
    shadow_a = alpha.filter(ImageFilter.GaussianBlur(radius * 0.6)).point(lambda v: int(v * 0.55))
    shadow = Image.new('RGBA', tile.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_a)
    off = int(s * 0.03)
    pad.alpha_composite(glow)
    pad.alpha_composite(shadow, (off, off))
    pad.alpha_composite(tile)
    return pad

def three_designs(sheet, soft=False):
    tile = 3600
    kinds = [('badge', PAL[0], 'GOOD'), ('star', PAL[1], 'DTF'), ('banner', PAL[3], 'PRINT')]
    ys = [250, 4050, 7850]
    for (kind, pal, label), y in zip(kinds, ys):
        t = design(kind, tile, pal, label)
        if soft:
            t = soften(t, (255, 214, 10), 150)
        sheet.alpha_composite(t, ((W - tile) // 2, y))

def save(im, name, dpi):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    im.save(path, dpi=(dpi, dpi), optimize=False, compress_level=6)
    print(name, im.size, im.mode, os.path.getsize(path) // 1024, 'KB')

def main(which):
    if 'transparent' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0)); three_designs(s); save(s, 'transparent.png', 300)
    if 'white' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0)); three_designs(s)
        bg = Image.new('RGB', (W, H), (255, 255, 255)); bg.paste(s, mask=s.getchannel('A')); save(bg, 'white-bg.png', 300)
    if 'glow' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0)); three_designs(s, soft=True); save(s, 'glow.png', 300)
    if 'gang' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        kinds = ['badge', 'star', 'banner', 'heart', 'bolt', 'jersey', 'sun']
        labels = {'badge': ['GOOD', 'CHILL', 'HAPPY', 'FRESH'], 'star': ['DTF', 'WOW', 'TOP', 'YES'], 'banner': ['PRINT', 'MERCH', 'STYLE', 'DREAM'],
                  'heart': ['LOVE', 'MOM', 'DAD', 'BFF'], 'bolt': ['POWER', 'FAST', 'BOOST', 'ZAP'], 'jersey': ['07', '10', '23', '99'], 'sun': ['SUN', 'BEACH', 'GOA', 'TRIP']}
        cols, rows = 4, 7
        cw, ch = W // cols, H // rows
        tile = int(min(cw, ch) * 0.86)
        i = 0
        for r in range(rows):
            for c in range(cols):
                kind = kinds[i % len(kinds)]
                label = labels[kind][(i // len(kinds)) % 4]
                t = design(kind, tile, PAL[(i + r) % len(PAL)], label)
                s.alpha_composite(t, (c * cw + (cw - tile) // 2, r * ch + (ch - tile) // 2))
                i += 1
        save(s, '28-designs.png', 300)
    if 'canva1x' in which:
        w, h = 2189, 3744
        s = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        tile = 1480
        for (kind, pal, label), y in zip([('badge', PAL[0], 'GOOD'), ('star', PAL[1], 'DTF')], [260, 1980]):
            s.alpha_composite(design(kind, tile, pal, label), ((w - tile) // 2, y))
        save(s, 'canva-1x.png', 96)
    if 'canvapro' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0)); three_designs(s); save(s, 'canva-3.125x.png', 300)
    if 'relabel' in which:
        w, h = 3420, 5850
        s = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        tile = 2300
        for (kind, pal, label), y in zip([('badge', PAL[0], 'GOOD'), ('star', PAL[1], 'DTF')], [400, 3100]):
            s.alpha_composite(design(kind, tile, pal, label), ((w - tile) // 2, y))
        save(s, '300-dpi.png', 300)
    if 'full300' in which:
        s = Image.new('RGBA', (W, H), (0, 0, 0, 0)); three_designs(s); save(s, '6840px.png', 300)

if __name__ == '__main__':
    main(sys.argv[1:] or ['transparent', 'white', 'glow', 'gang', 'canva1x', 'canvapro', 'relabel', 'full300'])
