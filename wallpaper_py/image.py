import argparse
from pathlib import Path
from PIL import Image

from .cli_parsers import existing_file_type
from .desktop_protocol import ImageMode


DESTINATION = Path(__file__).parent.joinpath("processed")


def process_stretch(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Resize the image to exactly fill target dimensions (stretch mode)."""
    return img.resize(target_size)


def process_fit(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """
    Resize the image to fit within target dimensions (keeping aspect ratio),
    and then center it on a black background.
    """
    target_width, target_height = target_size
    width_ratio = target_width / img.width
    height_ratio = target_height / img.height
    ratio = min(width_ratio, height_ratio)
    new_width = int(img.width * ratio)
    new_height = int(img.height * ratio)

    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    new_img = Image.new("RGB", target_size, (0, 0, 0))  # Black background
    x = (target_width - new_width) // 2
    y = (target_height - new_height) // 2
    new_img.paste(resized, (x, y))
    return new_img


def process_fill(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """
    Resize the image such that it completely fills the target dimensions
    (keeping aspect ratio), then crop the excess.
    """
    target_width, target_height = target_size
    img_ratio = img.width / img.height
    target_ratio = target_width / target_height

    if img_ratio > target_ratio:
        # Image is wider than target: scale by height
        new_height = target_height
        new_width = int(img.width * (new_height / img.height))
    else:
        # Image is taller than target: scale by width
        new_width = target_width
        new_height = int(img.height * (new_width / img.width))

    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left = (resized.width - target_width) / 2
    top = (resized.height - target_height) / 2
    right = left + target_width
    bottom = top + target_height
    return resized.crop((left, top, right, bottom))


def process_image(
    source_path: Path, target_width: int, target_height: int, mode: ImageMode
) -> Path:
    """Process image to fit the target resolution based on the specified mode."""
    img = Image.open(source_path)
    target_size = (target_width, target_height)

    if mode == ImageMode.STRETCH:
        processed_img = process_stretch(img, target_size)
    elif mode == ImageMode.FIT:
        processed_img = process_fit(img, target_size)
    elif mode == ImageMode.FILL:
        processed_img = process_fill(img, target_size)
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    output_path = (
        DESTINATION
        / f"{mode.name} {target_width}x{target_height} {source_path.stem}.png"
    )
    processed_img.save(output_path)
    return output_path


def image_mode_parse(arg: str) -> ImageMode:
    return ImageMode[arg.upper()]


def main() -> None:
    """CLI entry point for testing image processing"""
    parser = argparse.ArgumentParser(description="Test image processing for wallpapers")
    parser.add_argument(
        "source", type=existing_file_type, help="Path to source image file"
    )
    parser.add_argument("width", type=int, help="Target width in pixels")
    parser.add_argument("height", type=int, help="Target height in pixels")
    parser.add_argument(
        "mode",
        type=image_mode_parse,
        choices=list(ImageMode),
        help="Processing mode: FILL, FIT, STRETCH",
    )
    parser.add_argument("-o", "--output", help=f"Output path (default: {DESTINATION})")

    args = parser.parse_args()

    # Process image
    result_path = process_image(args.source, args.width, args.height, args.mode)
    print(f"Processed image saved to: {result_path}")


if __name__ == "__main__":
    main()
