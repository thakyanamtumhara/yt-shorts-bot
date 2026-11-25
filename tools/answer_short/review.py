"""The last look before an answer Short is published: the finished file itself, frame by frame.

Machine checks first (length, picture size, sound level, every line shown), then one model look at a sheet of real
frames from the finished video beside the cover, with the script and the cited facts. Anything wrong = not published.
"""

import base64
import io
import json
from pathlib import Path
import subprocess

from PIL import Image

from tools.answer_short.render import COVER_HOLD, W, H


class ReviewHold(RuntimeError):
    pass


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width,height',
                          '-of', 'json', str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next(stream for stream in data['streams'] if stream['codec_type'] == 'video')
    return {'seconds': float(data['format']['duration']), 'width': video['width'], 'height': video['height'],
            'audio': any(stream['codec_type'] == 'audio' for stream in data['streams'])}


def loudness(path):
    text = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json',
                           '-f', 'null', '-'], capture_output=True, text=True).stderr
    levels = json.loads(text[text.rindex('{'):text.rindex('}') + 1])
    return float(levels['input_i']), float(levels['input_tp'])


def machine_problems(final, report, voice):
    problems = []
    facts = probe(final)
    if (facts['width'], facts['height']) != (W, H):
        problems.append(f"picture is {facts['width']}x{facts['height']}, not {W}x{H}")
    if not facts['audio']:
        problems.append('the file has no sound')
    if not 18 <= facts['seconds'] <= 58:
        problems.append(f"the video is {facts['seconds']:.1f} s; an answer Short is 18-58 s")
    if abs(facts['seconds'] - report['seconds']) > 0.4:
        problems.append('the file length differs from the plan')
    level, peak = loudness(final)
    if not -16.5 <= level <= -12.5 or peak > -0.8:
        problems.append(f'sound level {level:.1f} LUFS / peak {peak:.1f} dBTP is outside the target')
    if len(report['pictures']) != len(voice['sentences']):
        problems.append('a spoken line has no picture')
    return problems, {**facts, 'lufs': level, 'peak': peak}


def frame_at(path, seconds):
    out = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{seconds:.2f}', '-i', str(path), '-frames:v', '1', '-vf', 'scale=432:-1',
                          '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'], capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(out)).convert('RGB')


def contact_sheet(final, report, out_path):
    """Cover, hook, the middle of every line, and the end card, in order, 5 across."""
    times = [0.2, COVER_HOLD + min(1.4, report['hook_end'] - 0.4)]
    times += [COVER_HOLD + (picture['start'] + picture['end']) / 2 for picture in report['pictures'][1:]]
    for spans in report['cards'].values():
        times.append(COVER_HOLD + spans[0][0] + 0.6)
    times.append(report['seconds'] - 0.5)
    times = sorted(set(round(value, 2) for value in times))
    frames = [frame_at(final, value) for value in times]
    columns = 5
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * 432, rows * 768), 'white')
    for index, frame in enumerate(frames):
        sheet.paste(frame, ((index % columns) * 432, (index // columns) * 768))
    sheet.save(out_path, quality=84)
    return times


def model_look(client, model, plan, sheet_path, cover_path, times):
    def picture(path):
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg' if str(path).endswith('.jpg') else 'image/png',
                                            'data': base64.b64encode(Path(path).read_bytes()).decode()}}
    cover = Image.open(cover_path).convert('RGB').resize((432, 768))
    small = Path(sheet_path).with_name('cover-small.jpg')
    cover.save(small, quality=85)
    cited = {key: fact['claim'] for key, fact in plan['evidence'].items()}
    text = f"""You are the final reviewer of a 25 to 45 second vertical business video before it is published. Picture 1 is a sheet
of real frames from the finished file, left to right, top to bottom, at these seconds: {times}. Picture 2 is its cover.

It should show: our real product videos and photos (plain T-shirts, hoodies, people holding or wearing them, a godown),
a yellow question panel near the start, word captions in the lower middle, a dark rate card with big yellow rupee
digits on some frames, a small "sale91.com" badge top left, and a dark end card with "sale91.com".

SPOKEN LINES: {json.dumps([line['say'] for line in plan['lines']], ensure_ascii=False)}
HOOK ON SCREEN: {plan['hook_text']}
COVER TEXT: {plan['cover']}
CITED FACTS: {json.dumps(cited, ensure_ascii=False)}

Reject for any of these, and only these: a frame that is black, blank, broken or only blur; text cut off at an edge,
overlapping other text, or unreadable; a rate card or caption covering a person's face; a vehicle number plate that
can be read; a rupee number on screen that
is not in the cited facts; a product on screen that is clearly a different kind of garment from the one the line is
about (a hoodie for a T-shirt question); a spelling mistake in the hook, cover or end card; anything a buyer would
find confusing in the first two seconds. Do not reject for taste.

Return ONE JSON object: {{"decision": "approve" or "reject", "problems": ["<each problem, with the second it is at>"],
"notes": "<one sentence on what you saw>"}}"""
    content = [picture(sheet_path), picture(small), {'type': 'text', 'text': text}]
    verdict = None
    # Newer models think before they answer: a small token allowance came back with no text at all (first cloud
    # test, 25-Nov-2026). Generous allowance, and one more try before the run is held.
    for attempt in (1, 2):
        response = client.messages.create(model=model, max_tokens=16000, messages=[{'role': 'user', 'content': content}])
        raw = '\n'.join(block.text for block in response.content if isinstance(getattr(block, 'text', None), str))
        start, end = raw.find('{'), raw.rfind('}')
        if start >= 0 and end > start:
            try:
                verdict = json.loads(raw[start:end + 1])
                break
            except ValueError:
                pass
        print(f"   final look attempt {attempt}: no JSON (stop {getattr(response, 'stop_reason', '?')}, "
              f"{len(raw)} chars: {raw[:120]!r})")
    if verdict is None:
        raise ReviewHold('the final look gave no readable answer')
    if verdict.get('decision') not in ('approve', 'reject') or not isinstance(verdict.get('problems'), list):
        raise ReviewHold('the final look gave no clear decision')
    return verdict


def check(client, model, plan, report, voice, work):
    final, work = Path(report['final']), Path(work)
    problems, measured = machine_problems(final, report, voice)
    sheet_path = work / 'review-sheet.jpg'
    times = contact_sheet(final, report, sheet_path)
    verdict = {'decision': 'reject', 'problems': problems, 'notes': 'machine checks failed'}
    if not problems:
        verdict = model_look(client, model, plan, sheet_path, report['cover'], times)
        if verdict['decision'] == 'approve' and verdict['problems']:
            verdict['decision'] = 'reject'
    result = {'decision': verdict['decision'], 'problems': verdict['problems'], 'notes': verdict.get('notes', ''),
              'measured': measured, 'frames_at': times}
    (work / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result
