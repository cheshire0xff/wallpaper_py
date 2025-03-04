#!/usr/bin/env python3
"""
Master helper script to run code quality checks.

This script sequentially runs:
  - Black formatting check
  - Mypy type checking
  - Pylint analysis

"""

import subprocess
import sys

DIRECTORIES = ["wallpaper_py", "tests", "scripts"]


def run_command(cmd: list[str], description: str) -> None:
    """
    Run a command using subprocess.run with check=True.

    Args:
        cmd: Command and arguments to run.
        description: A human-friendly description of the command.
    """
    print(f"Running {description}: {' '.join(cmd)}")
    process = subprocess.run(cmd, check=False)
    if process.returncode != 0:
        sys.exit(process.returncode)


def main() -> None:
    # Run Black formatting check (using --check so it won't modify files)
    run_command(
        ["black", "--check"] + DIRECTORIES,
        "Black formatting check",
    )

    # Run Mypy type checking on production and test directories
    run_command(
        ["mypy"] + DIRECTORIES,
        "Mypy type checking",
    )

    # Run Pylint analysis on production code, tests, and helper scripts
    run_command(
        ["pylint"] + DIRECTORIES,
        "Pylint analysis",
    )
    print("All code quality checks passed successfully.")


if __name__ == "__main__":
    main()
