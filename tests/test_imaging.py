from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from wallpaper_py.core.imaging import (
    background_colour,
    fill_box,
    fit_size,
    image_size,
    load,
    load_small,
    preview_size,
    render,
)
from wallpaper_py.core.model import Background, ImageLoadError, ImageMode, Look

RED, GREEN, BLUE, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)

# Every mode, and Fit with each kind of background.
LOOKS = [
    *(Look(mode) for mode in ImageMode),
    Look(ImageMode.FIT, "#3366cc"),
    *(Look(ImageMode.FIT, background) for background in Background),
    # Blur at the ends of its settings (core.model.MIN_BLUR, MAX_BLUR ...).
    Look(ImageMode.FIT, Background.BLUR, blur=0.001, darken=0.0),
    Look(ImageMode.FIT, Background.BLUR, blur=0.02, darken=0.8),
]
# The backgrounds that are one colour (all but blur).
COLOURS = [Background.AVERAGE, Background.DOMINANT, Background.EDGES]


def stripes(width: int = 300, height: int = 100) -> Image.Image:
    """Three vertical stripes: red | green | blue."""
    img = Image.new("RGB", (width, height))
    third = width // 3
    for i, color in enumerate((RED, GREEN, BLUE)):
        img.paste(color, (i * third, 0, (i + 1) * third, height))
    return img


def scene(width: int, height: int) -> Image.Image:
    """
    Stripes with a white square in the middle, red the widest: one most
    common colour (two about as common may make `dominant` differ between
    the file and its small copy, which is accepted).
    """
    img = stripes(width, height)
    img.paste(RED, (0, 0, width * 2 // 5, height))
    img.paste(
        (255, 255, 255),
        (width * 4 // 9, height * 3 // 8, width * 5 // 9, height * 5 // 8),
    )
    return img


def close_to(actual: object, expected: tuple[int, int, int], tol: int = 12) -> bool:
    assert isinstance(actual, tuple)
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


@pytest.mark.parametrize(
    ("src", "dst", "box"),
    [
        ((400, 100), (100, 100), (150.0, 0.0, 250.0, 100.0)),  # wider: crop sides
        ((100, 400), (100, 100), (0.0, 150.0, 100.0, 250.0)),  # taller: crop top/bottom
        ((1920, 1080), (3840, 2160), (0.0, 0.0, 1920.0, 1080.0)),  # same ratio
    ],
)
def test_fill_box(
    src: tuple[int, int], dst: tuple[int, int], box: tuple[float, ...]
) -> None:
    assert fill_box(src, dst) == pytest.approx(box)


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        ((400, 100), (100, 100), (100, 25)),
        ((100, 400), (100, 100), (25, 100)),
        ((1920, 1080), (1920, 480), (853, 480)),
    ],
)
def test_fit_size(
    src: tuple[int, int], dst: tuple[int, int], expected: tuple[int, int]
) -> None:
    assert fit_size(src, dst) == expected


def test_fill_keeps_the_center() -> None:
    result = render(stripes(), (100, 100), (100, 100), Look(ImageMode.FILL))
    assert result.size == (100, 100)
    for x in (5, 50, 94):
        assert close_to(result.getpixel((x, 50)), GREEN)


def test_fit_letterboxes_with_black() -> None:
    result = render(stripes(), (100, 100), (100, 100), Look(ImageMode.FIT))
    assert result.size == (100, 100)
    assert result.getpixel((50, 5)) == BLACK  # bar above
    assert result.getpixel((50, 94)) == BLACK  # bar below
    assert close_to(result.getpixel((5, 50)), RED)
    assert close_to(result.getpixel((94, 50)), BLUE)


def test_fit_fills_the_bars_with_the_background() -> None:
    look = Look(ImageMode.FIT, "#3366cc")
    result = render(stripes(), (100, 100), (100, 100), look)
    assert result.getpixel((50, 5)) == (0x33, 0x66, 0xCC)
    assert close_to(result.getpixel((50, 50)), GREEN)


@pytest.mark.parametrize(
    ("screen", "expected"),
    [
        ((900, 100), (128, 0, 128)),  # bars left and right: red and blue
        ((100, 300), (85, 85, 85)),  # above and below: every stripe, mixed
    ],
)
def test_the_edges_are_the_ones_next_to_the_bars(
    screen: tuple[int, int], expected: tuple[int, int, int]
) -> None:
    result = render(stripes(), screen, screen, Look(ImageMode.FIT, Background.EDGES))
    assert close_to(result.getpixel((0, 0)), expected, tol=2)


def test_the_edges_are_only_what_the_bars_touch() -> None:
    """The outer 1/128 of the image on each side, not what is further in."""
    img = Image.new("RGB", (1280, 400), BLUE)
    img.paste((240, 160, 0), (0, 0, 10, 400))  # 10 px: 1/128 of 1280
    img.paste((200, 0, 0), (1270, 0, 1280, 400))
    colour = background_colour(img, Background.EDGES, sides=True)
    assert close_to(colour, (220, 80, 0), tol=2)


def test_dominant_is_the_most_common_colour() -> None:
    """Not the average: the colour most of the image is, as it is."""
    img = Image.new("RGB", (400, 100), (200, 100, 50))  # 40%
    img.paste(GREEN, (160, 0, 280, 100))  # 30%
    img.paste(BLUE, (280, 0, 400, 100))  # 30%
    colour = background_colour(img, Background.DOMINANT, sides=True)
    assert close_to(colour, (200, 100, 50), tol=1)


def test_dominant_takes_similar_colours_together() -> None:
    """Many shades of one colour beat one other colour more common than each."""
    img = Image.new("RGB", (400, 100), BLUE)  # 40%
    for i, x in enumerate(range(160, 400, 40)):  # six shades of red, 10% each
        img.paste((250 - i * 4, 0, 0), (x, 0, x + 40, 100))
    colour = background_colour(img, Background.DOMINANT, sides=True)
    assert close_to(colour, (240, 0, 0), tol=2)


@pytest.mark.parametrize("darken", [0.0, 0.25, 0.8])
def test_blur_fills_the_bars_with_the_image_blurred(darken: float) -> None:
    """
    The bars show the image as Fill would, blurred and darkened as set, under the
    image itself, sharp.
    """
    look = Look(ImageMode.FIT, Background.BLUR, darken=darken)
    result = render(stripes(300, 100), (600, 100), (600, 100), look)
    # The image in the middle (150..450), sharp to its edges.
    assert close_to(result.getpixel((152, 50)), RED, tol=4)
    assert close_to(result.getpixel((447, 50)), BLUE, tol=4)
    # Filled to the screen, its stripes are 200 px each: beside the image,
    # red on the left and blue on the right, darker.
    dark = round(255 * (1 - darken))
    assert close_to(result.getpixel((20, 50)), (dark, 0, 0), tol=4)
    assert close_to(result.getpixel((580, 50)), (0, 0, dark), tol=4)


def test_more_blur_spreads_further() -> None:
    """The radius is the look's: next to the image, more blur mixes more."""
    screen = (1200, 100)  # the image at 450..750

    def mixed(blur: float) -> int:
        look = Look(ImageMode.FIT, Background.BLUR, blur=blur, darken=0.0)
        # In the left bar, beside the filled image's red/green border (400).
        pixel = render(stripes(300, 100), screen, screen, look).getpixel((395, 50))
        assert isinstance(pixel, tuple)
        return int(pixel[1])  # the green in the red

    assert mixed(0.001) < mixed(0.005) < mixed(0.02)


def test_average_is_the_whole_images() -> None:
    img = Image.new("RGB", (400, 100), RED)
    img.paste(BLUE, (0, 0, 100, 100))  # a quarter blue
    colour = background_colour(img, Background.AVERAGE, sides=True)
    assert close_to(colour, (191, 0, 64), tol=1)


def framed(width: int, height: int) -> Image.Image:
    """`scene` in a light grey frame thinner than the edges' strip (1/256)."""
    img = Image.new("RGB", (width, height), (230, 230, 230))
    frame = width // 256
    img.paste(scene(width - 2 * frame, height - 2 * frame), (frame, frame))
    return img


@pytest.mark.parametrize(
    ("image", "tol"), [(scene, 2), (framed, 5)], ids=["scene", "framed"]
)
def test_automatic_colours_dont_depend_on_the_images_size(
    tmp_path: Path, image: Callable[[int, int], Image.Image], tol: int
) -> None:
    """
    From the file or the previews' small copy (`load_small`, LANCZOS): the
    same tiny copy, so the same colour. Next to a thin frame a few levels
    off (4 measured), as the small copy blurs the image into the frame.
    """
    img = image(3840, 1280)
    source = tmp_path / "image.png"
    img.save(source)
    small = load_small(source, 480).image  # a preview's (thumbnails.py)
    for kind in COLOURS:
        for sides in (True, False):
            big = background_colour(img, kind, sides=sides)
            assert close_to(background_colour(small, kind, sides=sides), big, tol)


def test_stretch_keeps_everything() -> None:
    result = render(stripes(), (90, 200), (90, 200), Look(ImageMode.STRETCH))
    assert result.size == (90, 200)
    assert close_to(result.getpixel((5, 100)), RED)
    assert close_to(result.getpixel((84, 100)), BLUE)


def test_the_default_look_is_fill() -> None:
    assert Look() == Look(ImageMode.FILL)


@pytest.mark.parametrize("look", LOOKS, ids=str)
@pytest.mark.parametrize(
    ("screen", "output"),
    [
        ((1920, 1080), (480, 270)),
        ((1080, 1920), (270, 480)),
        ((2560, 1440), (481, 271)),
    ],
)
def test_a_preview_is_the_full_render_smaller(
    look: Look, screen: tuple[int, int], output: tuple[int, int]
) -> None:
    """
    The rule every look keeps: it depends on the screen, never on the
    output size. A feature measured in output pixels fails this.
    """
    img = scene(900, 400)  # a square: shapes show
    full = render(img, screen, screen, look)
    preview = render(img, screen, output, look)
    assert full.size == screen and preview.size == output
    expected = full.resize(output, Image.Resampling.LANCZOS)
    difference = ImageStat.Stat(ImageChops.difference(preview, expected)).mean
    assert max(difference) < 4, difference


@pytest.mark.parametrize("source_mode", ["RGB", "RGBA", "P", "L"])
def test_any_source_loads_as_rgb(tmp_path: Path, source_mode: str) -> None:
    source = tmp_path / "source.png"
    stripes().convert(source_mode).save(source)
    result = render(load(source, (64, 48)), (64, 48), (64, 48), Look())
    assert (result.size, result.mode) == ((64, 48), "RGB")


def test_large_jpeg_is_rendered_at_target_size(tmp_path: Path) -> None:
    source = tmp_path / "big.jpg"
    stripes(3000, 1000).save(source, quality=90)
    img = render(load(source, (300, 100)), (300, 100), (300, 100), Look())
    assert img.size == (300, 100)
    assert close_to(img.getpixel((150, 50)), GREEN, tol=20)


def test_a_jpeg_turned_by_exif_is_not_decoded_too_small(tmp_path: Path) -> None:
    """Stored 800x600 on its side: upright it is 600x800, enough for 400x300."""
    source = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # turn 90 degrees to show it upright
    stripes(800, 600).save(source, quality=90, exif=exif)
    img = load(source, (400, 300))
    assert img.size[0] < img.size[1]  # upright
    # Half scale (400x300 as stored) would be 300x400 upright: too narrow.
    assert img.size[0] >= 400 and img.size[1] >= 300


@pytest.mark.parametrize(
    ("screen", "side", "expected"),
    [
        ((3840, 2160), 480, (480, 270)),
        ((1080, 1920), 480, (270, 480)),
        ((5120, 1440), 960, (960, 270)),
    ],
)
def test_preview_size_keeps_the_screens_shape(
    screen: tuple[int, int], side: int, expected: tuple[int, int]
) -> None:
    assert preview_size(screen, side) == expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ((3000, 1000), (1440, 480)),  # wide: its height is the side
        ((1000, 3000), (480, 1440)),  # tall: its width is
        ((300, 200), (300, 200)),  # smaller already: never enlarged
    ],
)
def test_load_small_covers_a_square(
    tmp_path: Path, stored: tuple[int, int], expected: tuple[int, int]
) -> None:
    source = tmp_path / "image.png"
    stripes(*stored).save(source)
    small = load_small(source, 480)
    assert small.image.size == expected
    assert small.size == stored
    assert small.image.mode == "RGB"


def test_load_small_gives_the_size_as_shown(tmp_path: Path) -> None:
    source = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # turn 90 degrees to show it upright
    stripes(1600, 900).save(source, quality=90, exif=exif)
    small = load_small(source, 480)
    assert small.size == (900, 1600)
    assert small.image.size == (480, 853)
    assert image_size(source) == (900, 1600)  # the header alone says the same


def test_image_size_needs_an_image(tmp_path: Path) -> None:
    fake = tmp_path / "fake.jpg"
    fake.write_text("not an image", encoding="utf-8")
    with pytest.raises(ImageLoadError):
        image_size(fake)


@pytest.mark.parametrize("look", LOOKS, ids=str)
@pytest.mark.parametrize("screen", [(3840, 2160), (1080, 1920), (5120, 1440)])
def test_a_preview_from_the_small_copy_is_the_wallpaper_smaller(
    tmp_path: Path, look: Look, screen: tuple[int, int]
) -> None:
    """
    What the windows show (a small copy, rendered small) is the wallpaper
    the app sets (the file, rendered at the screen's size), smaller: for
    every look, on any screen shape.
    """
    img = scene(2700, 1200)
    source = tmp_path / "image.png"
    img.save(source)
    side = 480
    output = preview_size(screen, side)
    wallpaper = render(load(source, screen), screen, screen, look)
    preview = render(load_small(source, side).image, screen, output, look)
    expected = wallpaper.resize(output, Image.Resampling.LANCZOS)
    difference = ImageStat.Stat(ImageChops.difference(preview, expected)).mean
    assert max(difference) < 4, difference
