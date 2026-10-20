"""Generate SQLVIP's Windows icon from code.

Run with Pillow installed:
    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 1024
RADIUS = 210


def _lerp(start: int, end: int, ratio: float) -> int:
    return round(start + (end - start) * ratio)


def create_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    gradient = Image.new("RGB", (SIZE, SIZE))
    draw_gradient = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = (
            _lerp(79, 107, ratio),
            _lerp(107, 77, ratio),
            _lerp(255, 245, ratio),
        )
        draw_gradient.line([(0, y), (SIZE, y)], fill=color)

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, SIZE - 1, SIZE - 1), radius=RADIUS, fill=255
    )
    canvas.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    stroke = 44
    white = (255, 255, 255, 255)

    # Database cylinder: three stacked rings, matching the web brand mark.
    draw.ellipse((300, 272, 724, 432), outline=white, width=stroke)
    draw.line((300, 352, 300, 720), fill=white, width=stroke)
    draw.line((724, 352, 724, 720), fill=white, width=stroke)
    draw.arc((300, 560, 724, 720), start=0, end=180, fill=white, width=stroke)
    draw.arc((300, 412, 724, 572), start=0, end=180, fill=white, width=stroke)

    # A small highlight on the top-left of the cylinder.
    draw.arc((320, 292, 560, 412), start=190, end=250, fill=(255, 255, 255, 150), width=18)
    return canvas


def main() -> None:
    source = create_icon()
    source.resize((512, 512), Image.Resampling.LANCZOS).save(ASSETS / "sqlvip.png")

    icon = source.resize((256, 256), Image.Resampling.LANCZOS)
    icon.save(
        ASSETS / "sqlvip.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Created {ASSETS / 'sqlvip.ico'}")


if __name__ == "__main__":
    main()
