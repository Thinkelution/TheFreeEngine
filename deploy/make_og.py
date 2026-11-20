"""Draw web/og.png (1200x630) for link previews on social sites and search."""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
img = Image.new("RGB", (W, H), (10, 10, 18))
glow = Image.new("RGB", (W, H), (0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse((-250, -300, 650, 500), fill=(90, 60, 200))
g.ellipse((700, 150, 1450, 900), fill=(20, 140, 170))
img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(160)), 0.55)
d = ImageDraw.Draw(img)


def font(size, bold=False):
    for name in (["seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# waveform mark
x0, mid = 90, 150
for i in range(28):
    h = 18 + 70 * abs(math.sin(i * 0.55)) * (0.55 + 0.45 * math.sin(i * 0.21))
    c = (int(139 + (34 - 139) * i / 27), int(108 + (211 - 108) * i / 27), int(255 + (238 - 255) * i / 27))
    d.rounded_rectangle((x0 + i * 16, mid - h / 2, x0 + i * 16 + 9, mid + h / 2), radius=4, fill=c)

d.text((90, 240), "The Free Engine", font=font(84, True), fill=(236, 235, 247))
d.text((92, 345), "Free text-to-speech, voice cloning & music API", font=font(40), fill=(190, 188, 215))
d.text((92, 410), "60 voices · 6 moods · free voice clone · open models", font=font(32), fill=(150, 148, 180))
d.rounded_rectangle((90, 500, 470, 560), radius=30, fill=(139, 108, 255))
d.text((118, 510), "thefreeengine.site", font=font(32, True), fill=(10, 10, 18))
img.save(sys.argv[1] if len(sys.argv) > 1 else "web/og.png", optimize=True)
print("og image written")
