"""Render brand-source/icon.svg into the PNGs Home Assistant reads from the integration's
brand/ folder: icon (dark on light) and dark_icon (white), at 256 and 512 px.

Run: .hatest/Scripts/python brand-source/render.py  (needs Pillow and Microsoft Edge)
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image

HERE = Path(__file__).parent
OUT = HERE.parent / "custom_components" / "neighborhood_watch" / "brand"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SIZE = 2048


def render(svg_text: str) -> Image.Image:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        svg = Path(tmp) / "icon.svg"
        png = Path(tmp) / "out.png"
        svg.write_text(svg_text, encoding="utf-8")
        subprocess.run(
            [
                EDGE,
                "--headless",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                "--disable-gpu",
                "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--window-size={SIZE},{SIZE}",
                f"--screenshot={png}",
                svg.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        for _ in range(120):  # Edge's launcher returns before the screenshot is written
            if png.exists() and png.stat().st_size > 0:
                time.sleep(0.5)
                break
            time.sleep(0.25)
        img = Image.open(png).convert("RGBA")
        img.load()
    return img


def square(img: Image.Image, pad: float = 0.04) -> Image.Image:
    box = img.getchannel("A").getbbox()
    img = img.crop(box)
    side = int(max(img.size) * (1 + 2 * pad))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def main() -> None:
    source = (HERE / "icon.svg").read_text(encoding="utf-8")
    light = source
    dark = source.replace("<svg ", '<svg fill="#ffffff" ', 1).replace("stroke:black", "stroke:white")
    OUT.mkdir(parents=True, exist_ok=True)
    for prefix, text in (("", light), ("dark_", dark)):
        art = square(render(text))
        for name in ("icon", "logo"):
            art.resize((256, 256), Image.LANCZOS).save(OUT / f"{prefix}{name}.png")
            art.resize((512, 512), Image.LANCZOS).save(OUT / f"{prefix}{name}@2x.png")
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
