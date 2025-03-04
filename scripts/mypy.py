#!/usr/bin/env python3
"""
Helper script to run mypy on specified directories.

This script forwards any additional command-line arguments directly to mypy.
By default, it runs mypy on the "wallpaper_py" and "tests" directories.
Usage:
    python scripts/run_mypy.py [--some-flag ...]
"""

import sys
import subprocess


def main() -> None:
    # Directories to check
    directories = ["wallpaper_py", "tests", "scripts"]
    # Build the mypy command with any additional arguments forwarded
    cmd = ["mypy"] + directories + sys.argv[1:]
    print(f"Running command: {' '.join(cmd)}")
    process = subprocess.run(cmd, check=False)
    sys.exit(process.returncode)


if __name__ == "__main__":
    main()
