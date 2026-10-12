"""Generate the Mighty Car-Wash loyalty card images (one per stamp count).

Usage:  python make_cards.py [background.jpg]
Writes cards/card-0.png ... cards/card-9.png. card-9 means the card is full
and the next (10th) wash is free.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

HERE = Path(__file__).parent
LOGO = HERE / "assets" / "logo.jpg"
OUT = HERE / "cards"
W, H = 1200, 675
GOAL = 10
FOOTER = "Bolobedu Matipane Molai"
FREE_LABEL = ("FREE", "WASH")

BLUE = (52, 170, 230)
BLUE_DARK = (10, 70, 110)
WHITE = (245, 248, 250)
INK = (20, 30, 40)

FONT_HEAVY = "C:/Windows/Fonts/ariblk.ttf"
FONT_SEMI = "C:/Windows/Fonts/seguisb.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def content_box(img, thresh=40):
    """Bounding box of the non-black part of the logo."""
    mask = img.convert("L").point(lambda v: 255 if v > thresh else 0)
    return mask.getbbox()


def black_to_alpha(img):
    """Use brightness as alpha so the logo's black background disappears."""
    rgba = img.convert("RGBA")
    lum = img.convert("L").point(lambda v: 0 if v < 25 else min(255, int((v - 25) * 3)))
    rgba.putalpha(lum)
    return rgba


def background(path):
    if path:
        bg = ImageOps.fit(Image.open(path).convert("RGB"), (W, H), Image.LANCZOS)
        shade = Image.new("RGB", (W, H), (0, 0, 0))
        bg = Image.blend(bg, shade, 0.55)          # darken so stamps stay readable
    else:
        bg = Image.new("RGB", (W, H), (4, 8, 14))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse((W * 0.15, H * 0.25, W * 0.85, H * 1.2), fill=120)
        glow = glow.filter(ImageFilter.GaussianBlur(140))
        bg = Image.composite(Image.new("RGB", (W, H), BLUE_DARK), bg, glow)
    return bg


def main():
    bg_path = sys.argv[1] if len(sys.argv) > 1 else None
    OUT.mkdir(exist_ok=True)

    logo_src = Image.open(LOGO).convert("RGB")
    full = logo_src.crop(content_box(logo_src))
    # car mark only: the part of the logo above the "MIGHTY" lettering
    car = logo_src.crop((150, 150, 1450, 590))
    car = car.crop(content_box(car))

    logo = black_to_alpha(full)
    logo.thumbnail((520, 230), Image.LANCZOS)
    car_icon = black_to_alpha(car)

    base = background(bg_path)
    d = ImageDraw.Draw(base)
    base.paste(logo, ((W - logo.width) // 2, 22), logo)

    # stamp grid: 2 rows x 5
    r = 58
    gap_x = 210
    xs = [W // 2 + (i - 2) * gap_x for i in range(5)]
    ys = [345, 492]
    centres = [(x, y) for y in ys for x in xs]

    fnum = font(FONT_HEAVY, 46)
    ffree = font(FONT_HEAVY, 25)
    ffoot = font(FONT_SEMI, 30)

    # footer is the same on every card
    d.line((W // 2 - 300, 590, W // 2 + 300, 590), fill=(90, 120, 140), width=2)
    # location pin + text, centred together
    tw = d.textlength(FOOTER, font=ffoot)
    px = int(W // 2 - (tw + 34) / 2)       # pin is ~22px wide plus 12px gap
    py = 628
    d.ellipse((px, py - 22, px + 22, py), fill=WHITE)
    d.polygon([(px + 2, py - 7), (px + 20, py - 7), (px + 11, py + 12)], fill=WHITE)
    d.ellipse((px + 6, py - 16, px + 16, py - 6), fill=INK)
    d.text((px + 34, py), FOOTER, font=ffoot, fill=WHITE, anchor="lm")

    icon = car_icon.copy()
    icon.thumbnail((int(r * 1.6), int(r * 1.6)), Image.LANCZOS)

    for count in range(GOAL):          # count = paid washes on the card (0..9)
        card = base.copy()
        cd = ImageDraw.Draw(card)
        for i, (cx, cy) in enumerate(centres, start=1):
            box = (cx - r, cy - r, cx + r, cy + r)
            if i == GOAL:
                full_card = count >= GOAL - 1
                cd.ellipse(box, fill=BLUE if full_card else WHITE, outline=BLUE, width=6)
                col = WHITE if full_card else BLUE_DARK
                cd.text((cx, cy - 15), FREE_LABEL[0], font=ffree, fill=col, anchor="mm")
                cd.text((cx, cy + 17), FREE_LABEL[1], font=ffree, fill=col, anchor="mm")
            elif i <= count:
                cd.ellipse(box, fill=(6, 12, 20), outline=BLUE, width=6)
                card.paste(icon, (cx - icon.width // 2, cy - icon.height // 2), icon)
            else:
                cd.ellipse(box, fill=WHITE, outline=(200, 215, 225), width=3)
                cd.text((cx, cy + 2), str(i), font=fnum, fill=INK, anchor="mm")
        card.save(OUT / f"card-{count}.png", optimize=True)
    print("wrote", GOAL, "cards to", OUT)


if __name__ == "__main__":
    main()
