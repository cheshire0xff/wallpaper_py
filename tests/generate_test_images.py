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
