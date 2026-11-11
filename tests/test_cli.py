from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py import cli
from wallpaper_py.core import paths
from wallpaper_py.core.backend import Backend

from .fakes import FakeBackend, make_monitor


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend(
        [make_monitor("Main", size=(3840, 2160)), make_monitor("FHD", attached=False)]
    )


@pytest.fixture(name="image")
def fixture_image(tmp_path: Path) -> Path:
    path = tmp_path / "wall.png"
    Image.new("RGB", (64, 64)).save(path)
    return path


@pytest.fixture(autouse=True)
def isolated_render_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "render_dir", lambda: tmp_path / "rendered")


def run(backend: Backend, *argv: str) -> int:
    return cli.main(list(argv), backend_factory=lambda: backend)


def test_list_hides_detached_monitors(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(backend, "list") == 0
    out = capsys.readouterr().out
    assert "Main" in out and "3840x2160" in out
    assert "FHD" not in out


def test_list_all_shows_detached_monitors(
    backend: FakeBackend, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(backend, "list", "--all") == 0
    assert "detached" in capsys.readouterr().out


def test_set_by_name(
    backend: FakeBackend, image: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(backend, "set", str(image), "-m", "main") == 0
    assert backend.set_calls[0].image == image
    assert "Set wall.png on Main" in capsys.readouterr().out


def test_set_with_mode_renders(
    backend: FakeBackend, image: Path, tmp_path: Path
) -> None:
    assert run(backend, "set", str(image), "-m", "main", "--mode", "FIT") == 0
    assert backend.set_calls[0].image.parent == tmp_path / "rendered"


def test_set_on_detached_monitor_fails_cleanly(
    backend: FakeBackend, image: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(backend, "set", str(image), "-m", "fhd") == 1
    assert "not attached" in capsys.readouterr().err
    assert not backend.set_calls


def test_set_by_number_is_refused(
    backend: FakeBackend, image: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(backend, "set", str(image), "-m", "0") == 1
    assert "numbers are not supported" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["set", "missing.png", "-m", "main"],
        ["set", "{image}", "-m", "main", "--mode", "tile"],
        ["set", "{image}"],
    ],
)
def test_bad_arguments_exit_with_usage_error(
    backend: FakeBackend, image: Path, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        run(backend, *[a.format(image=image) for a in argv])
    assert exc.value.code == 2
