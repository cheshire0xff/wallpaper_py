#!/usr/bin/env python3
"""
Helper script to run pylint on specified directories.

This script forwards any additional command-line arguments directly to pylint.
By default, it runs pylint on the "wallpaper_py" and "tests" directories.
Usage:
    python scripts/run_pylint.py [--some-flag ...]
"""

import sys
import subprocess


def main() -> None:
    directories = ["wallpaper_py", "tests", "scripts"]
    cmd = ["pylint"] + directories + sys.argv[1:]
    print(f"Running command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    process = subprocess.run(cmd, check=True)
    sys.exit(process.returncode)


if __name__ == "__main__":
    main()
