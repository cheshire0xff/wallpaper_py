#!/usr/bin/env python
"""
Helper script to run Black on specified directories.

This script forwards any additional command-line arguments directly to Black.
For example, to run a check without formatting, you can call:
    python scripts/run_black.py --check
"""

import subprocess
import sys
from pathlib import Path


def run_black_on_dirs(dirs: list[str]) -> None:
    # Build the command to run Black on the specified directories,
    # forwarding any additional arguments provided to this script.
    cmd = ["black"] + [str(Path(d)) for d in dirs] + sys.argv[1:]
    print(f"Running command: {' '.join(cmd)}")
    process = subprocess.run(cmd, check=False)
    sys.exit(process.returncode)


def main() -> None:
    # Directories to check; adjust as needed.
    directories = ["tests", "wallpaper_py", "scripts"]
    run_black_on_dirs(directories)


if __name__ == "__main__":
    main()
