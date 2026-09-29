from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path('static/icons')
OUT.mkdir(parents=True, exist_ok=True)

GREEN = (5, 150, 105)
WHITE = (255, 255, 255)


def make_icon(size, filename, text_ratio):
    img = Image.new('RGB', (size, size), GREEN)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=int(size * text_ratio))
    text = 'MP'
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    draw.text((x, y), text, font=font, fill=WHITE)
    img.save(OUT / filename)
    print('created', OUT / filename)


make_icon(192, 'icon-192.png', 0.45)
make_icon(512, 'icon-512.png', 0.45)
make_icon(512, 'icon-maskable-512.png', 0.32)  # extra padding for Android's shape masks
make_icon(180, 'apple-touch-icon.png', 0.45)