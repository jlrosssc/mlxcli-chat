#!/usr/bin/env python3
"""Generate assets/icon_1024.png and assets/AppIcon.icns (needs Pillow; icns step needs macOS iconutil)."""
import os, shutil, subprocess, sys
from PIL import Image, ImageDraw, ImageFilter

S = 1024
here = os.path.dirname(os.path.abspath(__file__))


def gradient(size, top, bottom):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = c
    return img


bg = gradient(S, (58, 123, 255), (122, 68, 224)).convert("RGBA")
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(bg, (0, 0), mask)

# soft shadow + white chat bubble with a tail and three typing dots
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
bubble = (196, 250, 828, 660)
sd.rounded_rectangle((bubble[0], bubble[1] + 26, bubble[2], bubble[3] + 26), radius=120, fill=(20, 10, 80, 120))
sd.polygon([(300, 640), (300, 800), (470, 660)], fill=(20, 10, 80, 120))
shadow = shadow.filter(ImageFilter.GaussianBlur(22))
icon = Image.alpha_composite(icon, shadow)
d = ImageDraw.Draw(icon)
d.rounded_rectangle(bubble, radius=120, fill=(255, 255, 255, 255))
d.polygon([(290, 630), (290, 790), (470, 640)], fill=(255, 255, 255, 255))
for cx in (380, 512, 644):
    d.ellipse((cx - 38, 455 - 38, cx + 38, 455 + 38), fill=(92, 96, 240, 255))

png = os.path.join(here, "icon_1024.png")
icon.save(png)

if sys.platform == "darwin" and shutil.which("iconutil"):
    iconset = os.path.join(here, "AppIcon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            icon.resize((px, px), Image.LANCZOS).save(os.path.join(iconset, name))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(here, "AppIcon.icns")], check=True)
    shutil.rmtree(iconset)
print("icon written")
