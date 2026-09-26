"""Rebuild our original app mark at native and browser sizes (Pillow only)."""
from pathlib import Path
from PIL import Image, ImageDraw

assets = Path(__file__).resolve().parents[1] / 'account_switcher/static/assets'
scale = 8
image = Image.new('RGBA', (64*scale, 64*scale))
draw = ImageDraw.Draw(image)
def box(values):
    return tuple(int(v*scale) for v in values)
draw.rounded_rectangle(box((1, 1, 63, 63)), radius=17*scale, fill='#282e2c', outline='#526259', width=scale)
# Two open rings form an S: moving between two accounts, one continuous route.
draw.arc(box((14, 13, 48, 43)), 175, 292, fill='#e5eee9', width=6*scale)
draw.line([box((29, 16)), box((46, 16))], fill='#e5eee9', width=6*scale)
draw.polygon([box((43, 9)), box((53, 16)), box((43, 23))], fill='#e5eee9')
draw.arc(box((16, 21, 50, 51)), -5, 112, fill='#87dbb2', width=6*scale)
draw.line([box((35, 48)), box((18, 48))], fill='#87dbb2', width=6*scale)
draw.polygon([box((21, 41)), box((11, 48)), box((21, 55))], fill='#87dbb2')
image = image.resize((256, 256), Image.Resampling.LANCZOS)
image.save(assets / 'switcher.png')
