import argparse
from enum import Enum
from pathlib import Path
from PIL import Image

from wallpaper_py.cli_parsers import existing_file_type


DESTINATION = Path(__file__).parent.joinpath("processed")


class ImageMode(Enum):
    FILL = "fill"
    FIT = "fit"
    STRETCH = "stretch"


def process_image(
    source_path: Path, target_width: int, target_height: int, mode: ImageMode
) -> Path:
    """Process image to fit the target resolution based on the specified mode."""
    img = Image.open(source_path)
    target_size = (target_width, target_height)

    if mode == ImageMode.STRETCH:
        processed_img = img.resize(target_size)
    elif mode == ImageMode.FIT:
        processed_img = img.copy()
        processed_img.thumbnail(target_size, Image.Resampling.LANCZOS)
        new_img = Image.new("RGB", target_size, (0, 0, 0))  # Black background
        x = (target_width - processed_img.width) // 2
        y = (target_height - processed_img.height) // 2
        new_img.paste(processed_img, (x, y))
        processed_img = new_img
    elif mode == ImageMode.FILL:
        img_ratio = img.width / img.height
        target_ratio = target_width / target_height
        if img_ratio > target_ratio:
            new_height = target_height
            new_width = int(img.width * (new_height / img.height))
        else:
            new_width = target_width
            new_height = int(img.height * (new_width / img.width))
        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        left = (img_resized.width - target_width) / 2
        top = (img_resized.height - target_height) / 2
        right = left + target_width
        bottom = top + target_height
        processed_img = img_resized.crop((left, top, right, bottom))
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    # Save to temporary file
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

    # Rename if output specified
    if args.output:
        output_path = Path(args.output)
        Path(result_path).rename(output_path)
        result_path = str(output_path)

    print(f"Processed image saved to: {result_path}")


if __name__ == "__main__":
    main()
