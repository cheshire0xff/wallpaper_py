from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py import cli
from wallpaper_py.core import paths
from wallpaper_py.core.backend import Backend

from .fakes import FakeBackend, FakePresence, make_images, make_monitor


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


class TestPlaylistCommands:
    @pytest.fixture(name="config")
    def fixture_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setattr(paths, "state_db", lambda: tmp_path / "state.sqlite3")
        monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
        make_images(tmp_path / "walls", "a.jpg", "b.jpg", "c.jpg")
        config = tmp_path / "config.toml"
        config.write_text(
            "[playlists.walls]\n"
            "directory = 'walls'\n"
            'monitors = ["Main"]\n'
            'order = "sorted"\n'
            'interval = "10m"\n',
            encoding="utf-8",
        )
        return config

    def run(self, backend: FakeBackend, config: Path, *argv: str) -> int:
        return cli.main(
            ["--config", str(config), *argv],
            backend_factory=lambda: backend,
            presence_factory=FakePresence,
        )

    def test_run_shows_the_first_image(
        self, backend: FakeBackend, config: Path
    ) -> None:
        assert self.run(backend, config, "run", "--ticks", "1") == 0
        assert [c.image.name for c in backend.set_calls] == ["a.jpg"]

    def test_next_prev_fav_remove(
        self,
        backend: FakeBackend,
        config: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert self.run(backend, config, "next") == 0
        assert self.run(backend, config, "next", "main") == 0  # by monitor
        assert self.run(backend, config, "prev", "walls") == 0
        assert self.run(backend, config, "prev") == 0
        assert self.run(backend, config, "fav") == 0
        assert self.run(backend, config, "remove") == 0
        out = capsys.readouterr().out.splitlines()
        assert out[:4] == [
            "walls: a.jpg on Main",
            "walls: b.jpg on Main",
            "walls: a.jpg on Main",
            "walls: no earlier image",
        ]
        assert "favourites.txt" in out[4]
        assert "(not deleted)" in out[5] and out[6] == "walls: b.jpg on Main"
        listed = (tmp_path / "data" / "to-remove.txt").read_text(encoding="utf-8")
        assert listed == f"{tmp_path / 'walls' / 'a.jpg'}\n"

    def test_status(
        self, backend: FakeBackend, config: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.run(backend, config, "next")
        assert self.run(backend, config, "status") == 0
        out = capsys.readouterr().out
        assert "walls  (sorted, every 10m, as is)" in out
        assert "Main (attached)" in out
        assert "upcoming:  b.jpg, c.jpg, a.jpg" in out
        assert "Rotation active" in out

    def test_missing_config_is_explained(
        self,
        backend: FakeBackend,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert self.run(backend, tmp_path / "none.toml", "next") == 1
        assert "No config file" in capsys.readouterr().err

    def test_next_on_detached_monitor_fails_cleanly(
        self, config: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        backend = FakeBackend([make_monitor("Main", attached=False)])
        assert self.run(backend, config, "next", "walls") == 1
        assert "attached" in capsys.readouterr().err
