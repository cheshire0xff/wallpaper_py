import logging
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py import cli
from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.ui.launcher import PAGE_NAMES
from wallpaper_py.core import paths
from wallpaper_py.core.backend import Backend
from wallpaper_py.core.service import Change, ListStats, PlaylistStats
from wallpaper_py.core.settings import Order, Settings
from wallpaper_py.protocol import (
    CommandRequest,
    ErrorReply,
    MarkResult,
    PlaylistDetails,
    RemoveResult,
    Request,
    ResetCycleRequest,
    ResetCycleResult,
    StatsRequest,
    StatsResult,
    StatusResult,
    UpcomingResult,
)


from .fakes import FakeBackend, FakePresence, make_monitor, write_settings


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
    assert "Set wall.png on Main (rendered: " in capsys.readouterr().out


@pytest.mark.parametrize("mode", [(), ("--mode", "FIT")])
def test_set_renders(
    backend: FakeBackend, image: Path, tmp_path: Path, mode: tuple[str, ...]
) -> None:
    assert run(backend, "set", str(image), "-m", "main", *mode) == 0
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
        ["run", "--poll", "nan"],
        ["run", "--poll", "inf"],
        ["run", "--poll", "0"],
        ["fav", "walls", "--image", "x.jpg"],  # one or the other
        ["unremove"],  # needs --image
        ["upcoming", "-n", "0"],
        ["upcoming", "-n", "1001"],
        ["upcoming", "-n", "many"],
    ],
)
def test_bad_arguments_exit_with_usage_error(
    backend: FakeBackend, image: Path, argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        run(backend, *[a.format(image=image) for a in argv])
    assert exc.value.code == 2


class TestPlaylistCommands:
    @pytest.fixture(name="settings")
    def fixture_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Settings:
        monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
        return write_settings(tmp_path)

    def run(self, backend: FakeBackend, *argv: str) -> int:
        """No daemon is running: playlist commands run here, as in real use."""
        return cli.main(
            list(argv),
            backend_factory=lambda: backend,
            presence_factory=FakePresence,
        )

    @pytest.mark.usefixtures("settings")
    def test_run_shows_the_first_image(
        self,
        backend: FakeBackend,
    ) -> None:
        assert self.run(backend, "run", "--ticks", "1") == 0
        assert [c.source and c.source.name for c in backend.set_calls] == ["a.jpg"]

    @pytest.mark.usefixtures("settings")
    def test_next_prev_fav_remove(
        self,
        backend: FakeBackend,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert self.run(backend, "next") == 0
        assert self.run(backend, "next", "main") == 0  # by monitor
        assert self.run(backend, "prev", "walls") == 0
        assert self.run(backend, "prev") == 0
        assert self.run(backend, "fav") == 0
        assert self.run(backend, "remove") == 0
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

    @pytest.mark.usefixtures("settings")
    def test_marks_by_image_and_upcoming(
        self,
        backend: FakeBackend,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        image = tmp_path / "walls" / "b.jpg"
        to_remove = tmp_path / "data" / "to-remove.txt"
        assert self.run(backend, "upcoming", "-n", "1") == 0
        assert capsys.readouterr().out == f"{tmp_path / 'walls' / 'a.jpg'}\n"
        assert self.run(backend, "fav", "--image", str(image)) == 0
        assert self.run(backend, "remove", "--image", str(image)) == 0
        assert to_remove.read_text(encoding="utf-8") == f"{image}\n"
        assert self.run(backend, "upcoming") == 0
        assert self.run(backend, "unremove", "--image", str(image)) == 0
        assert self.run(backend, "unremove", "--image", str(image)) == 0
        assert to_remove.read_text(encoding="utf-8") == ""
        assert capsys.readouterr().out.splitlines() == [
            f"Added {image} to {tmp_path / 'data' / 'favourites.txt'}",
            f"Listed {image} in {to_remove} (not deleted)",
            "A playlist showing it moves on once the app runs",  # run here: no tick
            str(tmp_path / "walls" / "a.jpg"),  # not b.jpg: it is listed
            str(tmp_path / "walls" / "c.jpg"),
            f"Removed {image} from {to_remove}",
            "b.jpg is not listed for removal",
        ]

    @pytest.mark.usefixtures("settings")
    def test_status(
        self,
        backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.run(backend, "next")
        assert self.run(backend, "status") == 0
        out = capsys.readouterr().out
        assert "walls  (sorted, every 10m)" in out
        assert "monitors:  Main (attached, fill)" in out
        assert "upcoming:  b.jpg, c.jpg, a.jpg" in out
        assert out.rstrip().endswith(cli.NOT_RUNNING)  # run here: no daemon

    @pytest.mark.usefixtures("settings")
    def test_pick_hold_resume(
        self,
        backend: FakeBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "elsewhere").mkdir()
        Image.new("RGB", (8, 8)).save(tmp_path / "elsewhere" / "x.png")
        monkeypatch.chdir(tmp_path / "elsewhere")  # a relative path is fine
        assert self.run(backend, "next") == 0
        assert self.run(backend, "pick", "x.png") == 0
        assert self.run(backend, "prev", "walls") == 0
        assert self.run(backend, "pick", "missing.png", "walls") == 1
        assert self.run(backend, "hold") == 0
        assert self.run(backend, "hold", "walls") == 0
        assert self.run(backend, "status") == 0
        assert self.run(backend, "resume") == 0
        assert self.run(backend, "resume") == 0
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
        assert "walls  (sorted, every 10m, held)" in out
        assert "  next in:   held (10m of active time once resumed)" in out
        assert out[-2:] == ["walls: rotating again", "walls is not held"]
        missing = tmp_path / "elsewhere" / "missing.png"
        assert captured.err == f"error: No such image file: {missing}\n"

    def test_no_settings_yet_is_no_playlists(
        self,
        backend: FakeBackend,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A new state database: nothing set up in Settings yet."""
        assert not paths.state_db().exists()
        argv = ["next"]
        assert cli.main(argv, lambda: backend, FakePresence) == 1
        assert "`wallpaper ui settings`" in capsys.readouterr().err

    @pytest.mark.usefixtures("settings")
    def test_stats_and_reset_cycle_of_a_sorted_playlist(
        self,
        backend: FakeBackend,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert self.run(backend, "next") == 0
        assert self.run(backend, "stats", "--rescan") == 0
        out = capsys.readouterr().out.splitlines()
        assert out[1:] == [
            "walls  (sorted; this walk: at 1 of 3)",
            f"  folder:  {tmp_path / 'walls'} (scanned just now)",
            "  images:  3  in the folder",
            "  seen:    1  the current image and those before it, in name order",
            "  unseen:  2  after the current image, in name order",
            "  skipped: 0  listed to remove, or unreadable",
            "  here:    0 favourites, 0 to remove",
            f"favourites: {tmp_path / 'data' / 'favourites.txt'}: 0 listed, "
            "0 available",
            f"to remove: {tmp_path / 'data' / 'to-remove.txt'}: 0 listed, "
            "0 available",
        ]
        # Sorted: nothing to ask, and the daemon refuses.
        assert self.run(backend, "reset-cycle", "walls") == 1
        assert "walls is sorted" in capsys.readouterr().err

    @pytest.mark.usefixtures("settings")
    def test_next_on_detached_monitor_fails_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        backend = FakeBackend([make_monitor("Main", attached=False)])
        assert self.run(backend, "next", "walls") == 1
        assert "attached" in capsys.readouterr().err


def test_playlist_command_gives_up_while_the_lock_stays_held(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No daemon answers, yet the lock is held (a stuck daemon or command)."""
    monkeypatch.setattr(cli, "WAIT_TIMEOUT_S", 0.5)
    lock = InstanceLock(paths.instance_lock())
    assert lock.acquire()
    try:
        assert cli.main(["next"]) == 1
    finally:
        lock.release()
    assert capsys.readouterr().err == (
        "error: wallpaper_py is starting or busy and did not answer; try again "
        f"(see {paths.log_file()})\n"
    )


def test_verbose_tells_how_the_command_ran(
    backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="wallpaper_py"):
        # No playlists yet: the command fails, but only after it ran here.
        assert cli.main(["--verbose", "next"], backend_factory=lambda: backend) == 1
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("Asking the running daemon (socket wallpaper_py-")
    assert messages[1] == "No daemon is running: running the command here"


def test_answers_run_here_say_so() -> None:
    status = StatusResult(playlists=(), paused=None)
    assert cli.format_answer(cli.Answer(status)).out == "Rotation active"
    here = cli.format_answer(cli.Answer(status, here=True))
    assert here.out == cli.NOT_RUNNING


def test_status_of_a_manual_playlist() -> None:
    strip = PlaylistDetails(
        id="strip-id",
        name="strip",
        monitors=(),
        current=None,
        favourite=False,
        manual=True,
        directory=Path("/p"),
        order=Order.SHUFFLE,
        interval=None,
        next_in_seconds=None,
        upcoming=(),
    )
    lines = cli.format_status([strip], None).splitlines()
    assert lines[0] == "strip  (shuffle, manual)"
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
    unremoved = MarkResult(
        command="unremove", playlist=None, image=image, list_file=file, changed=False
    )
    assert cli.format_reply(unremoved).out == "a.jpg is not listed for removal"

    def removed(
        playlist: str | None, change: Change | None, changed: bool = True
    ) -> list[str]:
        result = RemoveResult(
            playlist=playlist,
            image=image,
            list_file=file,
            changed=changed,
            change=change,
        )
        return cli.format_reply(result).out.splitlines()

    assert removed("w", None) == [
        f"Listed {image} in {file} (not deleted)",
        "w: changes once a monitor is attached",
    ]
    missed = Change("w", image, "next", ("A",), ("B",))
    assert removed("w", missed)[1] == "w: a.jpg on A (later on B)"
    assert removed(None, None, changed=False) == [
        "a.jpg is already listed for removal",
        "A playlist showing it moves on within a few seconds",
    ]
    upcoming = UpcomingResult(playlist="w", images=(image, Path("/p/b.jpg")))
    assert cli.format_reply(upcoming).out == f"{image}\n{Path('/p/b.jpg')}"
    assert cli.format_reply(UpcomingResult(playlist="w", images=())).out == (
        "w: nothing to come"
    )
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


def test_commands_go_to_the_daemon_client(capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[CommandRequest] = []

    def daemon_client(request: CommandRequest, _: cli.RunHere) -> cli.Answer:
        sent.append(request)
        status = StatusResult(playlists=(), paused=None, warnings=("w",))
        return cli.Answer(status)

    assert cli.main(["status"], daemon_client=daemon_client) == 0
    assert capsys.readouterr() == ("Rotation active\n", "Warning: w\n")
    assert sent == [Request(command="status")]


def test_ui_opens_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert options == [process.UiOptions("settings")]


class FakeDaemon:
    """A daemon that knows one shuffle playlist, "konachan"; records requests."""

    def __init__(self) -> None:
        self.sent: list[CommandRequest] = []

    def __call__(self, request: CommandRequest, _: cli.RunHere) -> cli.Answer:
        self.sent.append(request)
        if isinstance(request, StatsRequest):
            stats = PlaylistStats(
                name="konachan",
                directory=Path("/pictures/konachan"),
                order=Order.SHUFFLE,
                scan_age=150.0,
                images=20_431,
                seen=1_204,
                unseen=19_179,
                skipped=48,
                favourites_here=120,
                to_remove_here=48,
            )
            lists = ListStats(file=Path("/data/list.txt"), listed=3, available=2)
            return cli.Answer(
                StatsResult(playlists=(stats,), favourites=lists, to_remove=lists)
            )
        return cli.Answer(ResetCycleResult(playlist="konachan", unseen=20_383))


def test_stats_text(capsys: pytest.CaptureFixture[str]) -> None:
    daemon = FakeDaemon()
    assert cli.main(["stats", "konachan"], daemon_client=daemon) == 0
    assert daemon.sent == [StatsRequest(target="konachan")]
    out = capsys.readouterr().out.splitlines()
    assert out[:6] == [
        "konachan  (shuffle; this cycle: 6 % seen)",
        f"  folder:  {Path('/pictures/konachan')} (scanned 2 min ago)",
        "  images:  20,431  in the folder",
        "  seen:     1,204  shown in this shuffle cycle",
        "  unseen:  19,179  still to come before the next reshuffle",
        "  skipped:     48  listed to remove, or unreadable",
    ]
    assert out[6] == "  here:    120 favourites, 48 to remove"


@pytest.mark.parametrize(
    ("argv", "answer", "code", "sent"),
    [
        (["konachan"], "y", 0, [StatsRequest, ResetCycleRequest]),
        (["konachan"], "n", 1, [StatsRequest]),
        (["konachan"], None, 1, [StatsRequest]),  # no terminal: no
        (["konachan", "--yes"], None, 0, [ResetCycleRequest]),
    ],
)
def test_reset_cycle_asks_first(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    answer: str | None,
    code: int,
    sent: list[type],
) -> None:
    asked: list[str] = []

    def fake_input(question: str) -> str:
        asked.append(question)
        if answer is None:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    daemon = FakeDaemon()
    assert cli.main(["reset-cycle", *argv], daemon_client=daemon) == code
    assert [type(r) for r in daemon.sent] == sent
    out = capsys.readouterr().out
    if "--yes" in argv:
        assert not asked
    else:
        assert asked == [
            "Start konachan's shuffle cycle over? All 20,383 images become "
            "unseen. [y/N] "
        ]
    if code == 0:
        assert out == "konachan: a new shuffle cycle; all 20,383 images unseen\n"
    else:
        assert out.endswith("Nothing changed\n")
