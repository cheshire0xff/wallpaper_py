from pathlib import Path
import argparse


def existing_file_type(arg: str) -> Path:
    path = Path(arg)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"{path} is not an existing file!")
    return path
