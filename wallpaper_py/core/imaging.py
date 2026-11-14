"""
Render an image as it looks on a screen (a `Look`: fill / fit / stretch).

Every wallpaper is rendered to its monitor's exact resolution (core.setter),
so the system only ever shows exact-size images and every look is the
same on every platform. The windows' previews are the same render at a
small output (`preview_size`), from a small copy of the image
(`load_small`). The renderer knows nothing of the service, the backend or
where the result goes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .model import ImageLoadError, ImageMode, Look

BACKGROUND = (0, 0, 0)

# Renders are saved as BMP: lossless like PNG, but with no compression to
# compute (a 4K render saves in ~15 ms instead of ~150 ms). The cost is disk:
# ~25 MB per 4K render, two slots per monitor (see core.setter), saved there.
RENDER_SUFFIX = ".bmp"

_ORIENTATION = 0x0112  # the EXIF tag


Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Small:
    """A small copy of an image file (`load_small`), for previews."""

    image: Image.Image  # RGB, upright
    size: tuple[int, int]  # the file's own size, upright


def load(source: Path, size: tuple[int, int]) -> Image.Image:
    """
    Decode `source` as RGB, upright, and close the file before returning.
    JPEGs decode at a reduced scale that still covers `size`.

    Raises:
        ImageLoadError: missing, unreadable, truncated or not an image.
    """
    return _load(source, size)[0]


def load_small(source: Path, side: int) -> Small:
    """
    `source` made just big enough to cover a `side` x `side` square (its
    shorter side `side` px; never enlarged): enough for a preview whose
    longer side is `side` px, of any screen shape and look (even Fill on a
    portrait screen), and the file's own size from the same read.

    Raises:
        ImageLoadError: as `load`.
    """
    image, size = _load(source, (side, side))
    scale = side / min(image.size)
    if scale < 1:
        smaller = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        image = image.resize(smaller, Image.Resampling.LANCZOS, reducing_gap=3.0)
    return Small(image, size)


def _load(source: Path, size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """The image (see `load`) and the file's own size, upright."""
    try:
        with Image.open(source) as img:
            # JPEG only: decode at the smallest DCT scale that is still >= size.
            # The scale is chosen on the stored image: one EXIF turns on its
            # side (orientations 5-8) needs the size turned as well.
            shown, sideways = _upright_size(img)
            img.draft("RGB", size[::-1] if sideways else size)
            oriented = ImageOps.exif_transpose(img) or img
            image = oriented.convert("RGB")  # always a copy, detached from the file
            return image, shown
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as err:
        raise ImageLoadError(f"Cannot load {source}: {err}") from err


def image_size(source: Path) -> tuple[int, int]:
    """
    The file's own size, upright, from the header only: known even for a
    file that fails to decode (truncated, still being written).

    Raises:
        ImageLoadError: missing, unreadable or not an image.
    """
    try:
        with Image.open(source) as img:
            return _upright_size(img)[0]
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as err:
        raise ImageLoadError(f"Cannot open {source}: {err}") from err


def _upright_size(img: Image.Image) -> tuple[tuple[int, int], bool]:
    """
    The size as shown (after the EXIF orientation, from the header), and
    whether that turns the stored image on its side (orientations 5-8).
    """
    sideways = img.getexif().get(_ORIENTATION, 1) in (5, 6, 7, 8)
    width, height = img.size
    return ((height, width) if sideways else (width, height)), sideways


def check_image(source: Path) -> None:
    """
    Make sure `source` is a file Pillow can open (it reads the header only).

    Raises:
        ImageLoadError: missing, not a file, or not an image Pillow knows.
    """
    if not source.is_file():
        raise ImageLoadError(f"No such image file: {source}")
    try:
        with Image.open(source):
            pass
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as err:
        raise ImageLoadError(f"Cannot open {source} as an image: {err}") from err


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


def preview_size(screen: tuple[int, int], side: int) -> tuple[int, int]:
    """A preview's output: the screen's shape, `side` px on its longer side."""
    scale = side / max(screen)
    return max(1, round(screen[0] * scale)), max(1, round(screen[1] * scale))


def render(
    img: Image.Image, screen: tuple[int, int], output: tuple[int, int], look: Look
) -> Image.Image:
    """
    `img` as it looks with `look` on a screen of `screen` pixels, drawn at
    `output` pixels (the screen's own size for a wallpaper, smaller for a
    preview; about the screen's aspect ratio either way).

    The rule that keeps previews true: a look depends on the screen, never
    on the output. Anything measured in pixels (a margin, a blur radius) is
    in screen pixels, worked out at the screen's size and scaled to the
    output; so a render at preview size is the full render, smaller.
    """
    lanczos = Image.Resampling.LANCZOS
    mode = look.mode
    if mode is ImageMode.STRETCH:
        return img.resize(output, lanczos)
    if mode is ImageMode.FILL:
        return img.resize(output, lanczos, box=fill_box(img.size, screen))
    if mode is ImageMode.FIT:
        inner = fit_size(img.size, screen)
        size = (
            max(1, round(inner[0] * output[0] / screen[0])),
            max(1, round(inner[1] * output[1] / screen[1])),
        )
        canvas = Image.new("RGB", output, BACKGROUND)
        offset = ((output[0] - size[0]) // 2, (output[1] - size[1]) // 2)
        canvas.paste(img.resize(size, lanczos), offset)
        return canvas
    raise ValueError(f"Unsupported mode: {mode}")
