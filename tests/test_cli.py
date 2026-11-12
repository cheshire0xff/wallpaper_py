import logging
import sys
from functools import partial
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py import cli
from wallpaper_py.app.ui.launcher import PAGE_NAMES
from wallpaper_py.commands import execute, open_service
from wallpaper_py.core import paths
from wallpaper_py.core.backend import Backend
from wallpaper_py.core.config import Order
from wallpaper_py.core.model import ImageMode
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    CommandRequest,
    ErrorReply,
    MarkResult,
    PlaylistDetails,
    ReloadRequest,
    ReloadResult,
    RemoveResult,
    Reply,
    StatusResult,
)

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
        """Playlist commands go to a stand-in for the app, in this process."""

        def app_client(request: CommandRequest, config_path: Path | None) -> Reply:
            assert config_path == config  # --config was given, so it is sent
            assert not isinstance(request, ReloadRequest)  # the app runs that
            with open_service(config_path, backend, FakePresence()) as service:
                return execute(service, request).reply

        def no_backend() -> Backend:
            raise AssertionError("playlist commands must not use a backend")

        return cli.main(
            ["--config", str(config), *argv],
            backend_factory=no_backend if argv[0] != "run" else lambda: backend,
            presence_factory=FakePresence,
            app_client=app_client,
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

    def test_pick_hold_resume(
        self,
        backend: FakeBackend,
        config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "elsewhere").mkdir()
        Image.new("RGB", (8, 8)).save(tmp_path / "elsewhere" / "x.png")
        monkeypatch.chdir(tmp_path / "elsewhere")  # a relative path is fine
        assert self.run(backend, config, "next") == 0
        assert self.run(backend, config, "pick", "x.png") == 0
        assert self.run(backend, config, "prev", "walls") == 0
        assert self.run(backend, config, "pick", "missing.png", "walls") == 1
        assert self.run(backend, config, "hold") == 0
        assert self.run(backend, config, "hold", "walls") == 0
        assert self.run(backend, config, "status") == 0
        assert self.run(backend, config, "resume") == 0
        assert self.run(backend, config, "resume") == 0
        captured = capsys.readouterr()
        out = captured.out.splitlines()
        assert out[:5] == [
            "walls: a.jpg on Main",
            "walls: x.png on Main",
            "walls: a.jpg on Main",
            "walls: held; only next / prev / pick change it until "
            "`wallpaper resume walls`",
            "walls is already held",
        ]
        assert "walls  (sorted, every 10m, as is, held)" in out
        assert "  next in:   held (10m of active time once resumed)" in out
        assert out[-2:] == ["walls: rotating again", "walls is not held"]
        missing = tmp_path / "elsewhere" / "missing.png"
        assert captured.err == f"error: No such image file: {missing}\n"

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


def test_playlist_command_explains_when_the_app_does_not_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "START_TIMEOUT_S", 0.5)
    started: list[Path] = []

    def app_that_exits(config: Path) -> list[str]:
        started.append(config)
        return [sys.executable, "-c", "pass"]

    client = partial(cli.send_to_app, app_command=app_that_exits)
    assert cli.main(["next"], app_client=client) == 1
    assert started == [paths.config_file()]
    assert capsys.readouterr().err == (
        f"error: could not start wallpaper_py; see {paths.log_file()}\n"
    )


def test_verbose_tells_how_the_app_was_reached(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli, "START_TIMEOUT_S", 0.5)
    client = partial(
        cli.send_to_app, app_command=lambda _: [sys.executable, "-c", "pass"]
    )
    with caplog.at_level(logging.INFO, logger="wallpaper_py"):
        assert cli.main(["--verbose", "next"], app_client=client) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("Asking the running app (socket wallpaper_py-")
    assert messages[1] == "No app is running: starting one"
    assert messages[2].startswith("Starting ")


def test_status_of_a_manual_playlist() -> None:
    strip = PlaylistDetails(
        name="strip",
        monitors=(),
        current=None,
        favourite=False,
        manual=True,
        directory=Path("/p"),
        order=Order.SHUFFLE,
        interval=None,
        mode=ImageMode.FILL,
        next_in_seconds=None,
        upcoming=(),
    )
    lines = cli.format_status([strip], None).splitlines()
    assert lines[0] == "strip  (shuffle, manual, fill)"
    assert "  next in:   only by next / prev / pick (manual)" in lines


def test_pick_parses_an_absolute_path(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(["pick", str(tmp_path / "x.png"), "strip"])
    assert (args.image, args.target) == (tmp_path / "x.png", "strip")


def test_reply_texts() -> None:
    image, file = Path("/p/a.jpg"), Path("/d/favourites.txt")

    def unfav(changed: bool) -> MarkResult:
        return MarkResult(
            command="unfav", playlist="w", image=image, list_file=file, changed=changed
        )

    assert cli.format_reply(unfav(True)) == cli.Text(0, f"Removed {image} from {file}")
    assert cli.format_reply(unfav(False)) == cli.Text(0, "a.jpg is not a favourite")
    removed = RemoveResult(playlist="w", image=image, list_file=file, change=None)
    assert cli.format_reply(removed).out.splitlines() == [
        f"Listed {image} in {file} (not deleted)",
        "w: changes once a monitor is attached",
    ]
    missed = Change("w", image, "next", ("A",), ("B",))
    removed = RemoveResult(playlist="w", image=image, list_file=file, change=missed)
    assert cli.format_reply(removed).out.splitlines()[1] == "w: a.jpg on A (later on B)"
    error = ErrorReply.make("not_attached", "None of w's monitors is attached")
    assert cli.format_reply(error) == cli.Text(
        1, err="error: None of w's monitors is attached"
    )
    status = StatusResult(playlists=(), paused="idle", warnings=("x", "y"))
    assert cli.format_reply(status) == cli.Text(
        0, "Rotation paused: idle", "Warning: x\nWarning: y"
    )


def test_ui_pages_match_the_launcher() -> None:
    assert cli.PAGES == PAGE_NAMES
    args = cli.build_parser().parse_args(["ui"])
    assert args.page == "home"
    assert cli.build_parser().parse_args(["ui", "library"]).page == "library"


def test_reload_and_which_config_is_sent(capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[tuple[CommandRequest, Path | None]] = []

    def app_client(request: CommandRequest, config: Path | None) -> Reply:
        sent.append((request, config))
        return ReloadResult(warnings=("w",))

    assert cli.main(["reload"], app_client=app_client) == 0
    assert capsys.readouterr() == ("Config reloaded\n", "Warning: w\n")
    # No --config: none is sent, so the running app's own is meant.
    assert sent == [(ReloadRequest(), None)]
    assert cli.main(["--config", "x.toml", "reload"], app_client=app_client) == 0
    assert sent[-1][1] == Path("x.toml")


def test_ui_without_config_follows_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # pylint: disable=import-outside-toplevel
    from wallpaper_py.app import processes
    from wallpaper_py.app.ui import process

    monkeypatch.setattr(processes, "setup_logging", lambda _: None)  # no log file
    options: list[process.UiOptions] = []

    def run_ui(given: process.UiOptions) -> int:
        options.append(given)
        return 0

    monkeypatch.setattr(process, "run_ui", run_ui)
    assert cli.main(["ui", "settings"]) == 0
    assert options == [process.UiOptions(None, "settings")]
