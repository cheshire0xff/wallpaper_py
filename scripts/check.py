#!/usr/bin/env python3
"""
Run the project's code-quality checks (the same ones CI runs).

    python scripts/check.py               # all: black --check, mypy, pylint, pytest
    python scripts/check.py black         # format the code
    python scripts/check.py mypy [ARGS]   # one tool; extra arguments are forwarded

Tools run through the current interpreter (`python -m ...`), so the active
virtual environment's versions are used regardless of PATH.
"""

import subprocess
import sys
from collections.abc import Sequence

TARGETS = ["wallpaper_py", "tests", "scripts"]
TOOLS: dict[str, list[str]] = {
    "black": ["black", *TARGETS],
    "mypy": ["mypy", *TARGETS],
    "pylint": ["pylint", *TARGETS],
    "pytest": ["pytest", "-q"],
}
ALL_CHECKS: list[tuple[str, list[str]]] = [
    ("black", ["--check"]),
    ("mypy", []),
    ("pylint", []),
    ("pytest", []),
]


def run(tool: str, extra: Sequence[str]) -> int:
    cmd = [sys.executable, "-m", *TOOLS[tool], *extra]
    print(f"$ {' '.join(cmd[2:])}", flush=True)
    return subprocess.run(cmd, check=False).returncode


def main(argv: Sequence[str]) -> int:
    if not argv:
        for tool, extra in ALL_CHECKS:
            if code := run(tool, extra):
                return code
        print("All checks passed.")
        return 0
    tool, *extra = argv
    if tool not in TOOLS:
        print(f"Unknown tool {tool!r}; choose from: {', '.join(TOOLS)}")
        return 2
    return run(tool, extra)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
