"""Generate placeholder avatar icons for the two girls (resource/*.png).

Run with the Pillow-capable bundled Python. Replace these icons with
real character art later — just overwrite the same filenames.
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource")
SIZE = 512

EMOJI_FONT = "C:/Windows/Fonts/seguiemj.ttf"
TEXT_FONT = "C:/Windows/Fonts/msyh.ttc"


def gradient(w, h, top_rgb, bottom_rgb):
    img = Image.new("RGB", (w, h))
    dr = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        dr.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def make_icon(name, emoji, top, bottom, label):
    img = gradient(SIZE, SIZE, top, bottom)
    dr = ImageDraw.Draw(img)

    # Soft circle vignette
    dr.ellipse([40, 40, SIZE - 40, SIZE - 40], fill=(255, 255, 255, 0),
               outline=(255, 255, 255), width=6)

    # Big centered emoji (color)
    try:
        ef = ImageFont.truetype(EMOJI_FONT, 240)
        dr.text((SIZE // 2, 225), emoji, font=ef, anchor="mm", embedded_color=True)
    except Exception as exc:  # font unavailable → plain text fallback
        print(f"[warn] emoji font failed: {exc}", file=sys.stderr)
        dr.text((SIZE // 2, 225), emoji, fill="white", anchor="mm")

    # Label at bottom
    tf = ImageFont.truetype(TEXT_FONT, 56)
    # simple shadow for readability
    dr.text((SIZE // 2 + 3, 418 + 3), label, font=tf, anchor="mm", fill=(30, 30, 60))
    dr.text((SIZE // 2, 418), label, font=tf, anchor="mm", fill="white")

    path = os.path.join(OUT_DIR, name)
    img.save(path, "PNG")
    print(f"saved {path}")


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    # 千问大小姐: violet→rose, fan
    make_icon("qwen.png", "\U0001FAAD", (124, 91, 235), (236, 72, 153), "千问大小姐")
    # 小鲸鱼: deep blue→cyan, whale
    make_icon("deepseek.png", "\U0001F40B", (30, 64, 175), (34, 211, 238), "小鲸鱼")
