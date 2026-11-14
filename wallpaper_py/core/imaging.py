"""
Render an image as it looks on a screen (a `Look`: fill / fit / stretch,
and the background of Fit's bars).

Every wallpaper is rendered to its monitor's exact resolution (core.setter),
so the system only ever shows exact-size images and every look is the
same on every platform. The windows' previews are the same render at a
small output (`preview_size`), from a small copy of the image
(`load_small`). The renderer knows nothing of the service, the backend or
where the result goes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageEnhance, ImageFilter, ImageOps, ImageStat

from .model import Background, Colour, ImageLoadError, ImageMode, Look

# Automatic colours come from a copy of the image this small, never from
# the output: from the full file or from a preview's small copy, it is about
# the same, so a wallpaper and its preview get the same colour. Squashed to a
# square: each edge (one column or row) is then 1/128 of the image's side.
TINY = 128  # px, both sides
LEVELS = 8  # per channel, for `dominant`: 512 boxes of similar colours

# `blur`: the bars are the image filled to the screen, blurred by the look's
# part of the screen's longer side and darkened (`Look.blur`, `Look.darken`).
BLUR_WORK = 8  # px: the radius at the size the blur is worked out at

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
        sides = inner[0] < screen[0]  # bars left and right, else above and below
        canvas = _bars(img, screen, output, look, sides=sides)
        offset = ((output[0] - size[0]) // 2, (output[1] - size[1]) // 2)
        canvas.paste(img.resize(size, lanczos), offset)
        return canvas
    raise ValueError(f"Unsupported mode: {mode}")


RGB = tuple[int, int, int]


def _bars(
    img: Image.Image,
    screen: tuple[int, int],
    output: tuple[int, int],
    look: Look,
    *,
    sides: bool,
) -> Image.Image:
    """Fit's canvas at `output`, before the image goes on it: its bars."""
    background = look.background
    if background is Background.BLUR:
        radius = look.blur * max(screen) * output[0] / screen[0]  # in output pixels
        # Blurred at a size where the radius is BLUR_WORK px, then enlarged:
        # the same to the eye (at most a few levels off), 5x faster at 4K.
        scale = min(1.0, BLUR_WORK / radius)
        work = (max(1, round(output[0] * scale)), max(1, round(output[1] * scale)))
        filled = img.resize(work, Image.Resampling.BOX, box=fill_box(img.size, screen))
        blurred = filled.filter(ImageFilter.GaussianBlur(radius * work[0] / output[0]))
        darker = ImageEnhance.Brightness(blurred).enhance(1 - look.darken)
        return darker.resize(output, Image.Resampling.BICUBIC)
    return Image.new("RGB", output, background_colour(img, background, sides=sides))


def background_colour(
    img: Image.Image, background: Background | Colour, *, sides: bool
) -> RGB:
    """
    The colour of Fit's bars: a fixed one, or worked out from a tiny copy
    of `img` (`TINY`): its average, its most common colour, or the average
    of its edges next to the bars (`sides`: the left and right ones, else
    the top and bottom). Not for `blur`, which is no one colour (`_bars`).
    """
    if not isinstance(background, Background):
        red, green, blue = ImageColor.getrgb(background)[:3]
        return red, green, blue
    tiny = img.resize((TINY, TINY), Image.Resampling.BOX)
    if background is Background.AVERAGE:
        return _rgb(ImageStat.Stat(tiny).mean)
    if background is Background.DOMINANT:
        return _dominant(tiny)
    if background is Background.EDGES:
        last = TINY - 1
        if sides:
            boxes = ((0, 0, 1, TINY), (last, 0, TINY, TINY))
        else:
            boxes = ((0, 0, TINY, 1), (0, last, TINY, TINY))
        # Two strips of one size: the mean of their means is theirs.
        first, second = (ImageStat.Stat(tiny.crop(box)).mean for box in boxes)
        return _rgb((a + b) / 2 for a, b in zip(first, second))
    raise ValueError(f"Not one colour: {background}")


def _dominant(tiny: Image.Image) -> RGB:
    """
    The most common colour: the pixels sorted into boxes of similar colours
    (`LEVELS` per channel), the fullest box's average. When two boxes are
    about as full, the file and a preview's small copy may pick different
    ones (accepted: docs/architecture.md, "Image modes and looks").
    """
    step = 256 // LEVELS
    boxes: dict[RGB, list[int]] = {}  # box -> [pixels, red, green, blue sums]
    for count, pixel in tiny.getcolors(TINY * TINY) or []:
        assert isinstance(pixel, tuple)  # RGB: three channels
        red, green, blue = pixel
        box = boxes.setdefault((red // step, green // step, blue // step), [0] * 4)
        for i, value in enumerate((1, red, green, blue)):
            box[i] += value * count
    # The fullest; of equally full ones, the last by colour: no random pick.
    count, *sums = max(boxes.items(), key=lambda item: (item[1][0], item[0]))[1]
    return _rgb(total / count for total in sums)


def _rgb(values: Iterable[float]) -> RGB:
    red, green, blue = (round(value) for value in list(values)[:3])
    return red, green, blue
