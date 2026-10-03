import json, os, subprocess, sys
from PIL import Image
man, out = sys.argv[1], sys.argv[2]
items = json.load(open(man))
d = os.path.dirname(man)
zdir = os.path.join(d, 'z'); os.makedirs(zdir, exist_ok=True)
lines = []
cache = {}
for i, it in enumerate(items):
    f = it['f']
    if it['c']:
        x, y, w, h = it['c']
        key = (f, round(x, 1), round(y, 1), round(w, 1), round(h, 1))
        if key not in cache:
            im = Image.open(f).convert('RGB')
            z = im.resize((1080, 1920), Image.LANCZOS, box=(x, y, x + w, y + h))
            zf = os.path.join(zdir, f'z{len(cache):05d}.png'); z.save(zf); cache[key] = zf
        f = cache[key]
    lines.append(f"file '{f}'\nduration {it['d']:.5f}")
lines.append(f"file '{lines[-1].split(chr(39))[1]}'")
lst = os.path.join(d, 'list.txt'); open(lst, 'w').write('\n'.join(lines) + '\n')
total = sum(it['d'] for it in items)
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-t', f'{total:.3f}', '-vf', 'fps=30,scale=1080:1920:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-movflags', '+faststart', '-an', out], check=True)
print('encoded', out, f'{total:.2f}s', len(cache), 'zoom frames')
