#!/usr/bin/env python3
"""
Generate Hardcoded Test Images

This script creates two test images with fixed patterns:
- A horizontal gradient image (from black to white)
- A checkerboard pattern image

These images are saved in the tests/resources directory.
"""

from pathlib import Path
from PIL import Image, ImageDraw


def generate_gradient_image(width: int = 256, height: int = 256) -> Image.Image:
    """
    Generate a horizontal gradient image from black to white.

    Args:
        width (int): Image width in pixels. Defaults to 256.
        height (int): Image height in pixels. Defaults to 256.

    Returns:
        Image.Image: Generated gradient image.
    """
    image = Image.new("RGB", (width, height))
    for x in range(width):
        gray = int(255 * x / (width - 1))
        for y in range(height):
            image.putpixel((x, y), (gray, gray, gray))
    return image


def generate_checkerboard_image(
    width: int = 256, height: int = 256, block_size: int = 32
) -> Image.Image:
    """
    Generate a checkerboard pattern image.

    Args:
        width (int): Image width in pixels. Defaults to 256.
        height (int): Image height in pixels. Defaults to 256.
        block_size (int): Size of each checkerboard block. Defaults to 32.

    Returns:
        Image.Image: Generated checkerboard image.
    """
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, height, block_size):
        for x in range(0, width, block_size):
            if (x // block_size + y // block_size) % 2 == 0:
                draw.rectangle(
                    [x, y, x + block_size - 1, y + block_size - 1], fill="gray"
                )
    return image


def generate_library(
    directory: Path, count: int, size: tuple[int, int] = (640, 360)
) -> list[Path]:
    """
    A small wallpaper library for the grid's tests: `count` images of
    `size` (every fifth one portrait), JPEGs with a PNG every seventh, each
    a gradient in colours of its own, in natural name order.

    Args:
        directory (Path): Where to write them (created if needed).
        count (int): How many.
        size (tuple[int, int]): Width and height of a landscape one.

    Returns:
        list[Path]: The files written, in name order.
    """
    directory.mkdir(parents=True, exist_ok=True)
    return [_library_image(directory, index, size) for index in range(count)]


def _library_image(directory: Path, index: int, size: tuple[int, int]) -> Path:
    """`generate_library`'s image number `index` (from 0), written."""
    width, height = size[::-1] if index % 5 == 4 else size
    left = ((index * 67) % 256, (index * 131) % 256, (index * 29) % 256)
    right = (255 - left[0], left[2], left[1])
    ramp = Image.linear_gradient("L").resize((width, height))  # top to bottom
    image = Image.merge(
        "RGB",
        [
            ramp.point(lambda v, a=a, b=b: a + (b - a) * v // 255)
            for a, b in zip(left, right)
        ],
    )
    suffix = ".png" if index % 7 == 6 else ".jpg"
    path = directory / f"img{index + 1}{suffix}"
    image.save(path)
    return path


def save_image(image: Image.Image, output_path: Path) -> None:
    """
    Save the given image to the specified path, ensuring the directory exists.

    Args:
        image (Image.Image): The image to save.
        output_path (Path): The output file path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"Image saved to: {output_path}")


def main() -> None:
    # Determine the resources directory relative to this script
    resources_dir = Path(__file__).resolve().parent / "resources"

    # Generate test images
    gradient_img = generate_gradient_image()
    checkerboard_img = generate_checkerboard_image()

    # Define output paths
    gradient_path = resources_dir / "gradient_test_image.png"
    checkerboard_path = resources_dir / "checkerboard_test_image.png"

    # Save images
    save_image(gradient_img, gradient_path)
    save_image(checkerboard_img, checkerboard_path)


if __name__ == "__main__":
    main()
