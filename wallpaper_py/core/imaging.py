"""
Render an image to an exact monitor size (fill / fit / stretch).

Used when the platform can't apply a different mode per monitor (Windows):
an image rendered to the monitor's exact resolution looks the same whatever
the global mode is.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .model import ImageMode

BACKGROUND = (0, 0, 0)


Box = tuple[float, float, float, float]


def load(source: Path, size: tuple[int, int]) -> Image.Image:
    """Decode `source` as RGB, upright, and close the file before returning."""
    with Image.open(source) as img:
        # JPEG only: decode at the smallest DCT scale that is still >= size.
        img.draft("RGB", size)
        oriented = ImageOps.exif_transpose(img) or img
        return oriented.convert("RGB")  # always a copy, detached from the file


def fill_box(src: tuple[int, int], dst: tuple[int, int]) -> Box:
    """Centered crop box in `src` with the aspect ratio of `dst`."""
    sw, sh = src
    dw, dh = dst
    if sw * dh > dw * sh:  # source wider than target: crop the sides
        w = sh * dw / dh
        return ((sw - w) / 2, 0.0, (sw + w) / 2, float(sh))
    h = sw * dh / dw  # source taller: crop top and bottom
    return (0.0, (sh - h) / 2, float(sw), (sh + h) / 2)


def fit_size(src: tuple[int, int], dst: tuple[int, int]) -> tuple[int, int]:
    """Largest size with the aspect ratio of `src` that fits inside `dst`."""
    ratio = min(dst[0] / src[0], dst[1] / src[1])
    return max(1, round(src[0] * ratio)), max(1, round(src[1] * ratio))


def render(img: Image.Image, size: tuple[int, int], mode: ImageMode) -> Image.Image:
    lanczos = Image.Resampling.LANCZOS
    if mode is ImageMode.STRETCH:
        return img.resize(size, lanczos)
    if mode is ImageMode.FILL:
        return img.resize(size, lanczos, box=fill_box(img.size, size))
    if mode is ImageMode.FIT:
        inner = fit_size(img.size, size)
        canvas = Image.new("RGB", size, BACKGROUND)
        offset = ((size[0] - inner[0]) // 2, (size[1] - inner[1]) // 2)
        canvas.paste(img.resize(inner, lanczos), offset)
        return canvas
    raise ValueError(f"Unsupported mode: {mode}")


def render_file(
    source: Path, size: tuple[int, int], mode: ImageMode, destination: Path
) -> Path:
    """Render `source` to exactly `size` and save it as PNG at `destination`."""
    result = render(load(source, size), size, mode)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, "PNG", compress_level=1)
    return destination
