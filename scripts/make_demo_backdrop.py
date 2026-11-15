"""Draws bundles/demo/assets/backdrop.jpg, the fallback image of the demo bundle: a slate blue
gradient with a warm glow, computed here so the bundle carries no photo whose rights would need
clearing.

Usage: .venv/bin/python scripts/make_demo_backdrop.py
"""

import math
from pathlib import Path

from PIL import Image

TARGET = Path(__file__).resolve().parents[1] / "bundles" / "demo" / "assets" / "backdrop.jpg"
SIZE = (1080, 1350)
TOP = (34, 52, 74)
BOTTOM = (70, 110, 140)
GLOW = (242, 179, 61)
GLOW_CENTRE = (0.78, 0.3)
GLOW_RADIUS = 0.75


def pixel(x: float, y: float) -> tuple[int, int, int]:
    share = 0.65 * y + 0.35 * x
    base = [top + (bottom - top) * share for top, bottom in zip(TOP, BOTTOM, strict=True)]
    distance = math.hypot(x - GLOW_CENTRE[0], (y - GLOW_CENTRE[1]) * 1.25)
    glow = max(0.0, 1.0 - distance / GLOW_RADIUS) ** 2 * 0.85
    return tuple(
        round(value + (warm - value) * glow) for value, warm in zip(base, GLOW, strict=True)
    )


def main() -> None:
    width, height = SIZE
    image = Image.new("RGB", SIZE)
    image.putdata(
        [pixel(x / (width - 1), y / (height - 1)) for y in range(height) for x in range(width)]
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image.save(TARGET, "JPEG", quality=90, optimize=True)
    print(f"Wrote {TARGET} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
