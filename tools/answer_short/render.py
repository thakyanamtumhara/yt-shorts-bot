"""Builds the answer Short from real pictures: our own product videos and photos, full screen, and on a line or
two a real page of our own website shown as a phone screen (tools/answer_short/screens.py takes it).

No AI picture is made here. Each spoken line gets a real gallery clip or photo of the product it talks about; on
top go the hook (the buyer's question), word-by-word captions timed from the voice, the live rate card (digits read
from the rate list, never from the script), the small sale91.com badge and a 2 s end card. Pillow draws the layers,
one ffmpeg process encodes; nothing else is needed, so the same code runs on a laptop and on GitHub.
"""

import json
from pathlib import Path
import shutil
import subprocess
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
W, H, FPS = 1080, 1920, 30
COVER_HOLD, TAIL, OUTRO, OUTRO_FADE = 0.5, 0.6, 2.0, 0.3
LEAD = 0.08                      # a picture changes this long before its line starts
HOOK_MAX, HOOK_FADE = 2.6, 0.3
CAPTION_Y, CAPTION_MAX_W, CAPTION_SIZE, CAPTION_STROKE = 1250, 860, 78, 7
CARD_Y, CARD_W, CARD_FADE = 236, 936, 0.25
BADGE_XY = (48, 168)
PHOTO_W, PHOTO_TOP = 1240, 370   # a square photo, shown wider than the screen
UNDER_CARD_GAP, UNDER_CARD_BOTTOM = 14, 36
# A website screen: a rounded card with an address bar, always above the captions so no page text sits under
# them. A short page part is centred there; a long one scrolls slowly inside it.
SCREEN_W, SCREEN_TOP, SCREEN_BOTTOM = 960, 250, 1170
SCREEN_BAR, SCREEN_RADIUS, SCREEN_SPEED, SCREEN_WAIT = 88, 40, 85, 0.5
MUSIC = ROOT / 'bg_music' / 'answer_bed.m4a'
MUSIC_DB = -23
FONT = ROOT / 'assets' / 'fonts' / 'Baloo2.ttf'
WHITE, YELLOW, BLACK, INK = (255, 255, 255, 255), (255, 214, 10, 255), (0, 0, 0, 255), (20, 14, 34)
GREY = (214, 214, 222, 255)


class RenderHold(RuntimeError):
    pass


def font(size, weight='ExtraBold'):
    face = ImageFont.truetype(str(FONT), size)
    try:
        face.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return face


def check_font():
    face = font(60)
    if face.getmask('₹').getbbox() == face.getmask('￾').getbbox() and face.getmask('₹').getbbox() is None:
        raise RenderHold('the caption font has no rupee sign')


def text_size(draw, text, face, stroke=0):
    box = draw.textbbox((0, 0), text, font=face, stroke_width=stroke)
    return box[2] - box[0], box[3] - box[1], box


PROBE = ImageDraw.Draw(Image.new('RGBA', (8, 8)))


def fit(text, max_w, start, floor, weight='ExtraBold', stroke=0):
    size = start
    while True:
        face = font(size, weight)
        width, height, box = text_size(PROBE, text, face, stroke)
        if width <= max_w or size <= floor:
            return face, width, height, box
        size -= 4


def wrap(text, max_w, start, floor, max_lines=3, weight='ExtraBold'):
    """Break text into the fewest lines that fit at the biggest size."""
    words = text.split()
    for size in range(start, floor - 1, -4):
        face = font(size, weight)
        lines, current = [], ''
        for word in words:
            trial = (current + ' ' + word).strip()
            if text_size(PROBE, trial, face)[0] <= max_w or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        if len(lines) <= max_lines and all(text_size(PROBE, line, face)[0] <= max_w for line in lines):
            return face, lines
    return font(floor, weight), [text]


def panel(width, height, radius=44, fill=(14, 10, 26, 222)):
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=fill)
    return image


def hook_image(text):
    face, lines = wrap(text.upper(), 880, 104, 64, max_lines=3)
    line_h = int(face.size * 1.12)
    height = line_h * len(lines) + 90
    image = panel(980, height)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        width, _, box = text_size(draw, line, face)
        draw.text(((980 - width) // 2 - box[0], 40 + index * line_h), line, font=face, fill=YELLOW)
    return image


def rupee(value):
    return f'₹{value:,}'


def card_image(card):
    """The live rate card: product, its bulk rate in big digits, the sample rate, and what the rate leaves out."""
    tiers = card['tiers']
    rates = [tier['rate'] for tier in tiers]
    title_face, title_w, _, _ = fit(card['product'].upper(), CARD_W - 96, 62, 40)
    # Up to three size groups: the first group's rate big, every group in the rows below. More: the range.
    big_text = rupee(rates[0]) if len(tiers) <= 3 else f'{rupee(min(rates))} – {rupee(max(rates))}'
    big_face, big_w, big_h, big_box = fit(big_text, CARD_W - 96, 190, 110)
    small, tiny = font(44, 'Bold'), font(36, 'SemiBold')
    rows = []
    if len(tiers) == 1:
        rows.append(f"Sizes {tiers[0]['sizes']}")
    elif len(tiers) <= 3:
        rows += [f"{tier['sizes']}:  {rupee(tier['rate'])}" for tier in tiers]
    else:
        rows.append('Rate changes by size')
    rows.append(f"{card['bulk_from']}+ pieces, per piece")
    height = 70 + 70 + big_h + 40 + len(rows) * 58 + 70 + 40
    image = panel(CARD_W, height)
    draw = ImageDraw.Draw(image)
    y = 40
    draw.text((48, y), card['product'].upper(), font=title_face, fill=WHITE)
    y += 84
    draw.text((48 - big_box[0], y - big_box[1]), big_text, font=big_face, fill=YELLOW)
    y += big_h + 34
    for row in rows:
        draw.text((48, y), row, font=small, fill=WHITE)
        y += 58
    draw.text((48, y + 10), f"Sample (1 pc) {rupee(card['sample'])}  ·  Today's website rate  ·  GST extra", font=tiny, fill=GREY)
    return image


def badge_image():
    face = font(34, 'Bold')
    width, height, box = text_size(PROBE, 'sale91.com', face)
    image = panel(width + 36, height + 26, radius=18, fill=(0, 0, 0, 170))
    ImageDraw.Draw(image).text((18 - box[0], 13 - box[1]), 'sale91.com', font=face, fill=(255, 255, 255, 235))
    return image


def caption_image(words, active):
    size = CAPTION_SIZE
    while True:
        face = font(size)
        gap = int(size * 0.30)
        boxes = [PROBE.textbbox((0, 0), word, font=face, stroke_width=CAPTION_STROKE) for word in words]
        widths = [box[2] - box[0] for box in boxes]
        total = sum(widths) + gap * (len(words) - 1)
        if total <= CAPTION_MAX_W or size <= 48:
            break
        size -= 6
    height = max(box[3] for box in boxes) + CAPTION_STROKE
    pad = CAPTION_STROKE + 8
    image = Image.new('RGBA', (total + pad * 2, height + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = pad
    for index, (word, box, width) in enumerate(zip(words, boxes, widths)):
        draw.text((x - box[0], pad), word, font=face, fill=YELLOW if index == active else WHITE,
                  stroke_width=CAPTION_STROKE, stroke_fill=BLACK)
        x += width + gap
    return image


def scrim():
    """Soft dark bands so white text reads on any garment colour; the middle of the picture stays untouched."""
    image = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(0, 420):
        draw.line((0, y, W, y), fill=(0, 0, 0, int(110 * (1 - y / 420))))
    for y in range(1060, 1480):
        draw.line((0, y, W, y), fill=(0, 0, 0, int(70 * (1 - abs(y - 1270) / 210))))
    return image


def outro_image():
    image = Image.new('RGB', (W, H), INK)
    draw = ImageDraw.Draw(image)
    y = 700
    for text, size, colour, weight in (('sale91.com', 132, YELLOW, 'ExtraBold'),
                                       ('Plain T-shirts · Hoodies · DTF print', 54, WHITE, 'Bold'),
                                       ('1 piece sample · Bulk rate from 10 pieces', 46, GREY, 'SemiBold'),
                                       ('Delhi stock · Delivery all India', 46, GREY, 'SemiBold')):
        face, width, height, box = fit(text, 960, size, 36, weight)
        draw.text(((W - width) // 2 - box[0], y - box[1]), text, font=face, fill=colour)
        y += height + 46
    return image


def cover_image(base, lines):
    """Cover = a real frame of the product, darkened, with the subject and the buyer's question."""
    image = base.convert('RGB').resize((W, H), Image.LANCZOS)
    shade = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for y in range(H):
        centre = 1 - min(1.0, abs(y - 880) / 520)
        draw.line((0, y, W, y), fill=(0, 0, 0, int(60 + 150 * centre)))
    image = Image.alpha_composite(image.convert('RGBA'), shade)
    draw = ImageDraw.Draw(image)
    top_face, top_w, top_h, top_box = fit(lines[0].upper(), 980, 190, 96)
    face, wrapped = wrap(lines[1], 960, 124, 72, max_lines=2)
    line_h = int(face.size * 1.15)
    y = 880 - (top_h + 50 + line_h * len(wrapped)) // 2
    draw.text(((W - top_w) // 2 - top_box[0], y - top_box[1]), lines[0].upper(), font=top_face, fill=YELLOW,
              stroke_width=8, stroke_fill=BLACK)
    y += top_h + 50
    for line in wrapped:
        width, _, box = text_size(draw, line, face, 6)
        draw.text(((W - width) // 2 - box[0], y), line, font=face, fill=WHITE, stroke_width=6, stroke_fill=BLACK)
        y += line_h
    badge = badge_image()
    image.paste(badge, BADGE_XY, badge)
    return image.convert('RGB')


# ---------------------------------------------------------------- real pictures

def download(url, folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / url.rsplit('/', 1)[-1]
    if not path.is_file() or path.stat().st_size < 2000:
        request = urllib.request.Request(url, headers={'User-Agent': 'yt-shorts-bot answer short'})
        with urllib.request.urlopen(request, timeout=90) as response:
            path.write_bytes(response.read())
    if path.stat().st_size < 2000:
        raise RenderHold(f'picture did not download: {url}')
    return path


def probe_seconds(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def picture_key(folder, url):
    return folder + '/' + url.rsplit('/', 1)[-1]


def address_bar(domain):
    """The strip on top of a website screen: a lock and the site's name, like a phone browser."""
    bar = Image.new('RGB', (SCREEN_W, SCREEN_BAR), (236, 236, 242))
    draw = ImageDraw.Draw(bar)
    face = font(38, 'SemiBold')
    width = text_size(draw, domain, face)[0]
    pill = (60, 14, SCREEN_W - 60, SCREEN_BAR - 14)
    draw.rounded_rectangle(pill, radius=30, fill=(255, 255, 255))
    left = (SCREEN_W - width - 44) // 2
    middle = SCREEN_BAR // 2
    draw.rounded_rectangle((left, middle - 4, left + 26, middle + 16), radius=5, fill=(70, 70, 84))
    draw.arc((left + 4, middle - 20, left + 22, middle + 6), 180, 360, fill=(70, 70, 84), width=5)
    draw.text((left + 44, middle - 2), domain, font=face, fill=(40, 40, 52), anchor='lm')
    return bar


def by_colour_turn(photos):
    """Photos re-ordered so neighbours differ in colour (the gallery lists three of one colour in a row)."""
    groups = {}
    for item in photos:
        groups.setdefault(item.get('c') or '', []).append(item['u'])
    out, lists = [], list(groups.values())
    while any(lists):
        for group in lists:
            if group:
                out.append(group.pop(0))
    return out


def plan_pictures(plan, sheet, spans, recent=()):
    """One real picture per spoken line: the product's own clips and photos in turn (clip, clip, photo, ...).
    `recent` = pictures of the last published Shorts, oldest first: a clip or photo not shown lately goes first, so
    the same picture comes back as late as the gallery allows. A clip that comes round again inside one Short plays
    on from where it stopped, so no moment is shown twice."""
    from tools.answer_short.screens import SCREENS, key_for
    last_used = {key: index for index, key in enumerate(recent)}
    turn, cursor, shots = {}, {}, []
    taken = sheet.get('screen_files') or {}
    for line, (start, end) in zip(plan['lines'], spans):
        if line.get('screen') and key_for(line, sheet) in taken:
            key = key_for(line, sheet)
            shots.append({'kind': 'screen', 'url': '', 'path': taken[key], 'bar': SCREENS[line['screen']]['bar'],
                          'folder': 'screen', 'key': 'screen/' + key, 'start': start, 'end': end, 'from': 0.0, 'card': ''})
            continue
        folder = sheet['visuals'][line['show']]
        entry = sheet['gallery'][folder]
        fresh = lambda url: last_used.get(picture_key(folder, url), -1)
        videos = [('video', url) for url in sorted(entry['videos'], key=fresh)]
        photos = [('photo', url) for url in sorted(by_colour_turn(entry['photos']), key=fresh)[:4]]
        options = []
        for photo in photos or [None]:
            options += videos + ([photo] if photo else [])
        if not options:
            raise RenderHold(f'no real picture for {folder}')
        index = turn.get(folder, 0)
        kind, url = options[index % len(options)]
        turn[folder] = index + 1
        shots.append({'kind': kind, 'url': url, 'folder': folder, 'key': picture_key(folder, url), 'start': start,
                      'end': end, 'from': cursor.get(url, 0.0), 'card': line.get('card') or ''})
        cursor[url] = cursor.get(url, 0.0) + (end - start)
    return shots


def video_frames(path, start, need, out_dir):
    """Frames of a real clip for one line, scaled to fill the screen. A short clip is slowed a little, never looped."""
    length = probe_seconds(path)
    usable = max(1.0, length - 0.3)
    if start + need > usable:
        start = max(0.0, usable - need) if need <= usable else 0.0
    stretch = max(1.0, need / usable)
    if stretch > 1.6:
        raise RenderHold(f'{path.name} is too short ({length:.1f} s) for a {need:.1f} s line')
    out_dir.mkdir(parents=True, exist_ok=True)
    chain = f'setpts={stretch:.4f}*PTS,' if stretch > 1.001 else ''
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{need / stretch + 0.2:.3f}', '-i', str(path),
                    '-vf', f'{chain}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}',
                    '-frames:v', str(int(need * FPS) + 2), '-q:v', '2', str(out_dir / 'f%05d.jpg')], check=True)
    files = sorted(out_dir.glob('f*.jpg'))
    if len(files) < max(3, int(need * FPS) - 4):
        raise RenderHold(f'{path.name} gave {len(files)} frames for a {need:.1f} s line')
    return files


class Shot:
    def __init__(self, shot, work):
        self.start, self.end = shot['start'], shot['end']
        need = self.end - self.start
        path = Path(shot['path']) if shot['kind'] == 'screen' else download(shot['url'], work / 'assets')
        self.kind, self.name, self.key = shot['kind'], path.name, shot.get('key') or path.name
        if shot['kind'] == 'screen':
            page = Image.open(path).convert('RGB')
            back = page.resize((H, H), Image.LANCZOS).crop(((H - W) // 2, 0, (H - W) // 2 + W, H))
            self.back = back.filter(ImageFilter.GaussianBlur(60)).point(lambda value: int(value * 0.32))
            body = page.resize((SCREEN_W, max(1, round(page.height * SCREEN_W / page.width))), Image.LANCZOS)
            self.card = Image.new('RGB', (SCREEN_W, SCREEN_BAR + body.height), 'white')
            self.card.paste(address_bar(shot.get('bar') or 'sale91.com'), (0, 0))
            self.card.paste(body, (0, SCREEN_BAR))
            self.need = need
            self.window = min(self.card.height, SCREEN_BOTTOM - SCREEN_TOP)
            self.short = self.window == self.card.height
            self.mask = Image.new('L', (SCREEN_W, self.window), 0)
            ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, SCREEN_W - 1, self.window - 1), radius=SCREEN_RADIUS, fill=255)
            # A long page runs out under the card's bottom edge: a soft white fade there, so a cut line of page
            # text reads as "more below", not as a mistake.
            self.fade = Image.new('RGBA', (SCREEN_W, 90), (255, 255, 255, 0))
            for row in range(90):
                ImageDraw.Draw(self.fade).line((0, row, SCREEN_W, row), fill=(255, 255, 255, int(255 * (row / 89) ** 1.6)))
        elif shot['kind'] == 'video':
            self.files = video_frames(path, shot['from'], need, work / 'frames' / f"{len(list((work / 'frames').glob('*'))) if (work / 'frames').is_dir() else 0:02d}")
        else:
            photo = Image.open(path).convert('RGB')
            back = photo.resize((H, H), Image.LANCZOS).crop(((H - W) // 2, 0, (H - W) // 2 + W, H))
            self.back = back.filter(ImageFilter.GaussianBlur(40)).point(lambda value: int(value * 0.55))
            self.photo, self.need = photo, need
        # While a rate card is up, the picture sits below it (a clip is shown smaller on a dark ground), so the card
        # never covers a face, a collar or the hands holding the garment.
        self.under = shot.get('top')

    def frame(self, t):
        if self.kind == 'screen':
            image = self.back.copy()
            if self.short:
                top = SCREEN_TOP + (SCREEN_BOTTOM - SCREEN_TOP - self.window) // 2
                image.paste(self.card, ((W - SCREEN_W) // 2, top), self.mask)
                return image
            moved = min(self.card.height - self.window, int(SCREEN_SPEED * max(0.0, t - self.start - SCREEN_WAIT)))
            shown = Image.new('RGB', (SCREEN_W, self.window), 'white')
            shown.paste(self.card.crop((0, SCREEN_BAR + moved, SCREEN_W, moved + self.window)), (0, SCREEN_BAR))
            shown.paste(self.card.crop((0, 0, SCREEN_W, SCREEN_BAR)), (0, 0))
            if moved < self.card.height - self.window:
                shown.paste(self.fade, (0, self.window - 90), self.fade)
            image.paste(shown, ((W - SCREEN_W) // 2, SCREEN_TOP), self.mask)
            return image
        if self.kind == 'video':
            index = min(len(self.files) - 1, max(0, int((t - self.start) * FPS)))
            image = Image.open(self.files[index]).convert('RGB')
            if not self.under:
                return image
            height = H - self.under - UNDER_CARD_BOTTOM
            width = int(W * height / H)
            ground = Image.new('RGB', (W, H), INK)
            ground.paste(image.resize((width, height), Image.LANCZOS), ((W - width) // 2, self.under))
            return ground
        zoom = 1.0 + 0.06 * min(1.0, max(0.0, (t - self.start) / self.need))
        width = int(PHOTO_W * zoom)
        size = (width, int(width * self.photo.height / self.photo.width))
        shown = self.photo.resize(size, Image.LANCZOS)
        image = self.back.copy()
        top = self.under or PHOTO_TOP
        image.paste(shown, ((W - size[0]) // 2, top - (size[1] - int(PHOTO_W * self.photo.height / self.photo.width)) // 2))
        return image

    def first(self):
        return self.frame(self.start + min(1.5, (self.end - self.start) / 2))


def with_alpha(image, alpha):
    if alpha >= 0.999:
        return image
    out = image.copy()
    out.putalpha(out.getchannel('A').point(lambda value: int(value * alpha)))
    return out


NAME_STARTS = {'non', 'true', 'round', 'acid', 'zip', 'kids', 'premium', 'drop'}


def caption_lines(words):
    """Up to 3 words a line, a new line after punctuation. The first word of a product name never ends a line:
    "... Non" / "Bio ₹107 per" read as Bio at the Non Bio rate (26-Nov-2026), so "Non Bio ₹107" stays together."""
    lines, current = [], []
    for index, word in enumerate(words):
        if len(current) == 2 and index + 1 < len(words) and word['text'].strip('.,!?').lower() in NAME_STARTS \
                and not word['raw'].rstrip().endswith(('.', '!', '?', ',')):
            lines.append(current)
            current = []
        current.append(word)
        if len(current) >= 3 or word['raw'].rstrip().endswith(('.', '!', '?', ',')):
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


def caption_states(words, hook_end, body):
    lines = caption_lines(words)
    states, cache = [], {}
    for number, line in enumerate(lines):
        line_start, line_end = line[0]['start'], line[-1]['end'] + 0.18
        if number + 1 < len(lines):
            line_end = min(line_end, lines[number + 1][0]['start'])
        texts = [word['text'] for word in line]
        for index, word in enumerate(line):
            begin = max(line_start, word['start'])
            finish = max(line[index + 1]['start'] if index + 1 < len(line) else line_end, begin + 0.04)
            if finish <= hook_end:
                continue
            key = (tuple(texts), index)
            if key not in cache:
                cache[key] = caption_image(texts, index)
            states.append((max(begin, hook_end), min(finish, body), cache[key]))
    return states, [[word['text'] for word in line] for line in lines]


def render(plan, sheet, voice, work, final, recent=()):
    """Returns a report dict. `work` holds the pieces; `final` is the finished mp4; the cover is beside it."""
    check_font()
    work, final = Path(work), Path(final)
    work.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    sentences, words = voice['sentences'], voice['words']
    voice_end = max(word['end'] for word in words)
    body = voice_end + TAIL
    starts = [0.0] + [max(0.0, sentence['start'] - LEAD) for sentence in sentences[1:]]
    spans = [(start, starts[index + 1] if index + 1 < len(starts) else body) for index, start in enumerate(starts)]
    if any(end - start < 0.6 for start, end in spans):
        raise RenderHold('a spoken line is shorter than 0.6 s; the script is not one sentence per line')
    hook_end = min(HOOK_MAX, sentences[0]['end'] + 0.25)
    hook = hook_image(plan['hook_text'])
    cards = {}
    for line, (start, end) in zip(plan['lines'], spans):
        key = line.get('card') or ''
        if key:
            cards.setdefault(key, {'image': card_image(sheet['cards'][key]), 'spans': []})['spans'].append((max(start, hook_end), end))
    pictures = plan_pictures(plan, sheet, spans, recent)
    for picture in pictures:
        picture['top'] = CARD_Y + cards[picture['card']]['image'].height + UNDER_CARD_GAP if picture['card'] else None
    shutil.rmtree(work / 'frames', ignore_errors=True)
    shots = [Shot(picture, work) for picture in pictures]
    states, caption_lines = caption_states(words, hook_end, body)
    badge, shade, outro = badge_image(), scrim(), outro_image()
    cover = cover_image(shots[0].first(), plan['cover'])
    cover_path = final.with_name(final.stem + '-cover.png')
    cover.save(cover_path)
    total = COVER_HOLD + body + OUTRO
    frames = round(total * FPS)
    silent = work / 'silent.mp4'
    encoder = subprocess.Popen(
        ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
         '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
         '-level', '4.2', '-g', '60', '-movflags', '+faststart', str(silent)], stdin=subprocess.PIPE)
    last = None
    for number in range(frames):
        t = (number + 0.5) / FPS
        if t < COVER_HOLD:
            image = cover
        elif t < COVER_HOLD + body:
            at = t - COVER_HOLD
            shot = next((item for item in shots if item.start <= at < item.end), shots[-1])
            image = Image.alpha_composite(shot.frame(at).convert('RGBA'), shade)
            if at < hook_end:
                layer = with_alpha(hook, 1.0 if at < hook_end - HOOK_FADE else (hook_end - at) / HOOK_FADE)
                image.alpha_composite(layer, ((W - layer.width) // 2, 300))
            for card in cards.values():
                for start, end in card['spans']:
                    if start <= at < end:
                        layer = with_alpha(card['image'], min(1.0, (at - start) / CARD_FADE, (end - at) / CARD_FADE + 0.2))
                        image.alpha_composite(layer, ((W - CARD_W) // 2, CARD_Y))
            for start, end, layer in states:
                if start <= at < end:
                    image.alpha_composite(layer, ((W - layer.width) // 2, CAPTION_Y - layer.height // 2))
                    break
            image.alpha_composite(badge, BADGE_XY)
            image = image.convert('RGB')
            last = image
        else:
            fade = (t - COVER_HOLD - body) / OUTRO_FADE
            image = Image.blend(last, outro, min(1.0, fade)) if fade < 1 else outro
        encoder.stdin.write(image.tobytes())
    encoder.stdin.close()
    if encoder.wait() != 0:
        raise RenderHold('video encode failed')
    mix = work / 'mix.wav'
    delay = int(COVER_HOLD * 1000)
    music = ['-stream_loop', '3', '-i', str(MUSIC)] if MUSIC.is_file() else ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    subprocess.run(
        ['ffmpeg', '-v', 'error', '-y', '-i', str(work / 'voice.mp3'), *music, '-filter_complex',
         f"[0:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=60,afade=t=out:st={voice_end - 0.2:.3f}:d=0.2,adelay={delay}:all=1[v];"
         f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{body + OUTRO:.3f},volume={MUSIC_DB}dB,afade=t=in:st=0:d=0.5,"
         f"afade=t=out:st={body + OUTRO - 1.6:.3f}:d=1.6,adelay={delay}:all=1[m];"
         f"[v][m]amix=inputs=2:duration=longest:normalize=0,apad,atrim=0:{total:.3f}[a]",
         '-map', '[a]', '-c:a', 'pcm_s16le', str(mix)], check=True)
    measured = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(mix), '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json',
                               '-f', 'null', '-'], capture_output=True, text=True).stderr
    levels = json.loads(measured[measured.rindex('{'):measured.rindex('}') + 1])
    loudnorm = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={levels['input_i']}:measured_TP={levels['input_tp']}:"
                f"measured_LRA={levels['input_lra']}:measured_thresh={levels['input_thresh']}:offset={levels['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(silent), '-i', str(mix), '-map', '0:v', '-map', '1:a',
                    '-af', loudnorm + ',aresample=48000', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                    '-movflags', '+faststart', '-shortest', str(final)], check=True)
    silent.unlink(missing_ok=True)
    mix.unlink(missing_ok=True)
    report = {'final': str(final), 'cover': str(cover_path), 'seconds': round(total, 2), 'body': round(body, 2),
              'hook_end': round(hook_end, 2), 'music': MUSIC.is_file(),
              'pictures': [{'line': index + 1, 'kind': shot.kind, 'file': shot.name, 'key': shot.key, 'start': round(shot.start, 2),
                            'end': round(shot.end, 2)} for index, shot in enumerate(shots)],
              'cards': {key: [[round(a, 2), round(b, 2)] for a, b in card['spans']] for key, card in cards.items()},
              'caption_lines': caption_lines}
    (work / 'render.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return report
