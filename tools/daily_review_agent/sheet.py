import subprocess, sys, tempfile, os, glob
from PIL import Image, ImageDraw, ImageFont
src, step, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
cols = int(sys.argv[4]) if len(sys.argv) > 4 else 6
w = int(sys.argv[5]) if len(sys.argv) > 5 else 270
tmp = tempfile.mkdtemp()
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-vf', f'fps=1/{step},scale={w}:-2', f'{tmp}/s%04d.png'], check=True)
files = sorted(glob.glob(f'{tmp}/s*.png'))
ims = [Image.open(f).convert('RGB') for f in files]
fw, fh = ims[0].size
rows = (len(ims) + cols - 1) // cols
sheet = Image.new('RGB', (cols * (fw + 6), rows * (fh + 6)), 'white')
font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 18)
for i, im in enumerate(ims):
    d = ImageDraw.Draw(im)
    t = i * step
    d.rectangle([0, 0, 70, 24], fill='white')
    d.text((4, 2), f'{t:.1f}s', fill='red', font=font)
    sheet.paste(im, ((i % cols) * (fw + 6), (i // cols) * (fh + 6)))
sheet.save(out)
print(out, sheet.size, len(ims), 'frames')
