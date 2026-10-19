from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
OUT.mkdir(exist_ok=True)
SIZE = 512
image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)

# Rounded square with a cool cyan -> green gradient.
mask = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle((18, 18, SIZE - 18, SIZE - 18), radius=130, fill=255)
gradient = Image.new("RGBA", (SIZE, SIZE))
gd = ImageDraw.Draw(gradient)
for y in range(SIZE):
    for_x = y / (SIZE - 1)
    r = int(86 + (102 - 86) * for_x)
    g = int(231 + (240 - 231) * for_x)
    b = int(255 + (183 - 255) * for_x)
    gd.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))
image.paste(gradient, (0, 0), mask)

# Subtle glow inside the badge.
glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse((-80, -140, 400, 260), fill=(255, 255, 255, 36))
glow = glow.filter(ImageFilter.GaussianBlur(70))
image.alpha_composite(Image.composite(glow, Image.new("RGBA", (SIZE, SIZE)), mask))

# Paper plane.
plane = [
    (98, 276),
    (432, 96),
    (344, 430),
    (250, 318),
    (98, 276),
]
inner = [(250, 318), (432, 96), (196, 245)]
draw.polygon(plane, fill=(5, 24, 38, 242))
draw.polygon(inner, fill=(7, 46, 60, 255))
draw.line([(250, 318), (344, 430)], fill=(5, 24, 38, 0), width=0)
# Highlight along the fold.
draw.line([(196, 245), (432, 96)], fill=(255, 255, 255, 132), width=5)
draw.line([(98, 276), (250, 318)], fill=(255, 255, 255, 82), width=4)

# Small orbit accent.
draw.arc((55, 60, 475, 480), 205, 325, fill=(255, 255, 255, 85), width=5)
draw.ellipse((436, 380, 451, 395), fill=(255, 255, 255, 160))

image.save(OUT / "icon.png")
image.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
# The WebView can use the PNG directly as a favicon.
image.resize((128, 128), Image.Resampling.LANCZOS).save(ROOT / "web" / "icon.png")
print(OUT / "icon.png")
print(OUT / "icon.ico")
print(ROOT / "web" / "icon.png")
