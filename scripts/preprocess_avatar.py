"""Preprocess avatar sprites: chroma-key green screen, remove corner watermark, autocrop.

Usage:
    python scripts/preprocess_avatar.py [input_dir] [output_dir]

Defaults: input_dir = frontend/static/avatar/raw, output_dir = frontend/static/avatar
Place new character sprites (green background PNGs) in input_dir and run this script.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def key_green(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    w, h = rgba.size

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if g > 140 and r < 130 and b < 130:
                pixels[x, y] = (r, g, b, 0)
            else:
                if g > max(r, b) + 30:
                    pixels[x, y] = (r, max(r, b), b, a)

    for y in range(int(h * 0.90), h):
        for x in range(int(w * 0.60), w):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (r, g, b, 0)

    return rgba


def autocrop(img: Image.Image, margin: int = 8) -> Image.Image:
    bbox = img.getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(img.width, right + margin)
    bottom = min(img.height, bottom + margin)
    return img.crop((left, top, right, bottom))


def main():
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "frontend/static/avatar/raw"
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "frontend/static/avatar"
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(input_dir.glob("*.png"))
    if not files:
        print(f"No PNG files found in {input_dir}")
        sys.exit(1)

    for path in files:
        img = Image.open(path)
        keyed = key_green(img)
        cropped = autocrop(keyed)
        out = output_dir / path.name
        cropped.save(out)
        print(f"{path.name}: {img.size} -> {cropped.size} => {out}")


if __name__ == "__main__":
    main()
