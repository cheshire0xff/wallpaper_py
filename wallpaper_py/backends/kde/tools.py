"""Run KScreen's and systemd's command-line tools; their failures as WallpaperError."""

import sys

assert sys.platform == "linux"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import os
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from wallpaper_py.core.model import WallpaperError

# argv -> standard output; raises WallpaperError. Injected, so tests run
# the backend on recorded output.
Runner = Callable[[Sequence[str]], str]

# A tool that hangs (plasmashell busy or restarting) must not hold the
# service thread for long: the next tick tries again.
TIMEOUT = 10.0

PACKAGES = {
    "kscreen-doctor": "KScreen's tools (Arch: libkscreen)",
    "busctl": "systemd",
}


def run(argv: Sequence[str]) -> str:
    # kscreen-doctor is a Qt program: with QT_QPA_PLATFORM set (offscreen in
    # tests, xcb set by hand on Wayland) it picks a KScreen backend that
    # sees no outputs at all, and says nothing. Without it, it goes by the
    # session.
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",  # output that isn't UTF-8 is still readable
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
    except FileNotFoundError as err:
        package = PACKAGES.get(argv[0], argv[0])
        raise WallpaperError(f"{argv[0]} is missing: install {package}") from err
    except OSError as err:  # not executable, wrong format, ...
        raise WallpaperError(f"{argv[0]} can't be run: {err}") from err
    except subprocess.TimeoutExpired as err:
        raise WallpaperError(f"{argv[0]} did not answer in {TIMEOUT:g} s") from err
    if done.returncode != 0:
        message = done.stderr.strip() or f"exit code {done.returncode}"
        raise WallpaperError(f"{argv[0]} failed: {message}")
    return done.stdout


class _Reply(BaseModel):
    """`busctl --json=short call`'s output: the reply's signature and values."""

    model_config = ConfigDict(extra="ignore")

    type: str
    data: list[Any]


def call(
    run_tool: Runner,
    destination: str,
    path: str,
    method: str,
    arguments: Sequence[str] = (),
) -> list[Any]:
    """
    One D-Bus method call on the session bus; the reply's values.
    `method` is `interface.Name`; `arguments` in busctl's form: the
    signature, then the values (`["s", "text"]`).
    """
    interface, _, name = method.rpartition(".")
    argv = ["busctl", "--user", "--json=short", "call", destination, path]
    output = run_tool([*argv, interface, name, *arguments])
    if not output.strip():
        return []  # a method without a reply value
    try:
        reply: list[Any] = _Reply.model_validate_json(output).data
    except ValidationError as err:
        raise WallpaperError(f"busctl's output is not understood: {err}") from err
    return reply
