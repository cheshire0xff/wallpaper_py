from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core.imaging import fill_box, fit_size, render, render_file
from wallpaper_py.core.model import ImageMode

RED, GREEN, BLUE, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)


def stripes(width: int = 300, height: int = 100) -> Image.Image:
    """Three vertical stripes: red | green | blue."""
    img = Image.new("RGB", (width, height))
    third = width // 3
    for i, color in enumerate((RED, GREEN, BLUE)):
        img.paste(color, (i * third, 0, (i + 1) * third, height))
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
    result = render(stripes(), (100, 100), ImageMode.FILL)
    assert result.size == (100, 100)
    for x in (5, 50, 94):
        assert close_to(result.getpixel((x, 50)), GREEN)


def test_fit_letterboxes_with_black() -> None:
    result = render(stripes(), (100, 100), ImageMode.FIT)
    assert result.size == (100, 100)
    assert result.getpixel((50, 5)) == BLACK  # bar above
    assert result.getpixel((50, 94)) == BLACK  # bar below
    assert close_to(result.getpixel((5, 50)), RED)
    assert close_to(result.getpixel((94, 50)), BLUE)


def test_stretch_keeps_everything() -> None:
    result = render(stripes(), (90, 200), ImageMode.STRETCH)
    assert result.size == (90, 200)
    assert close_to(result.getpixel((5, 100)), RED)
    assert close_to(result.getpixel((84, 100)), BLUE)


@pytest.mark.parametrize("source_mode", ["RGB", "RGBA", "P", "L"])
def test_render_file_writes_exact_size_png(tmp_path: Path, source_mode: str) -> None:
    source = tmp_path / "source.png"
    stripes().convert(source_mode).save(source)
    out = render_file(source, (64, 48), ImageMode.FILL, tmp_path / "out" / "r.png")
    with Image.open(out) as img:
        assert (img.format, img.size, img.mode) == ("PNG", (64, 48), "RGB")


def test_large_jpeg_is_rendered_at_target_size(tmp_path: Path) -> None:
    source = tmp_path / "big.jpg"
    stripes(3000, 1000).save(source, quality=90)
    out = render_file(source, (300, 100), ImageMode.FILL, tmp_path / "r.png")
    with Image.open(out) as img:
        assert img.size == (300, 100)
        assert close_to(img.getpixel((150, 50)), GREEN, tol=20)
