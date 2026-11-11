"""PlaylistService with a fake backend, clock and presence."""

# pylint: disable=redefined-outer-name

import random
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core.config import Config, Order, PlaylistConfig
from wallpaper_py.core.library import Library
from wallpaper_py.core.model import ImageMode, MonitorNotAttachedError
from wallpaper_py.core.selection import MonitorSelectionError
from wallpaper_py.core.service import Change, PlaylistError, PlaylistService
from wallpaper_py.core.store import Store

from .fakes import FakeBackend, FakeClock, FakePresence, make_images, make_monitor

INTERVAL = 60.0
STEP = 5.0  # seconds between ticks, like `wallpaper run`


@dataclass
class Env:
    tmp: Path
    backend: FakeBackend
    clock: FakeClock
    presence: FakePresence
    store: Store
    config: Config
    service: PlaylistService

    @property
    def konachan(self) -> PlaylistConfig:
        return self.config.playlists[0]

    @property
    def fantasy(self) -> PlaylistConfig:
        return self.config.playlists[1]

    def tick_for(self, seconds: float) -> list[Change]:
        """Tick every STEP seconds; the first tick happens right away."""
        changes = self.service.tick()
        for _ in range(int(seconds / STEP)):
            self.clock.advance(STEP)
            changes += self.service.tick()
        return changes

    def restart(self) -> None:
        """A new process on the same state (e.g. after a reboot)."""
        self.service = make_service(self)

    def shown(self, monitor: str) -> Path | None:
        found = next(m for m in self.backend.monitors() if m.name == monitor)
        return found.wallpaper


def make_service(env: Env, tick_period: float = STEP) -> PlaylistService:
    return PlaylistService(
        env.config,
        env.backend,
        env.presence,
        env.store,
        env.tmp / "rendered",
        clock=env.clock,
        rng=random.Random(0),
        library=Library(env.clock, max_age=0),
        tick_period=tick_period,
    )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    make_images(tmp_path / "konachan", *(f"k{i:02}.jpg" for i in range(1, 9)))
    make_images(tmp_path / "fantasy", "f1.png", "f2.png", "f10.png")
    config = Config(
        playlists=(
            PlaylistConfig(
                "konachan",
                tmp_path / "konachan",
                ("AW3225QF", "PLX2783H"),
                INTERVAL,
                Order.SHUFFLE,
            ),
            PlaylistConfig(
                "fantasy", tmp_path / "fantasy", ("ZeroMOD",), INTERVAL, Order.SORTED
            ),
        ),
        favourites_file=tmp_path / "lists" / "favourites.txt",
        to_remove_file=tmp_path / "lists" / "to-remove.txt",
        pause_when_idle=300.0,
    )
    environment = Env(
        tmp=tmp_path,
        backend=FakeBackend(
            [
                make_monitor("AW3225QF", size=(3840, 2160)),
                make_monitor("PLX2783H", x=-1920),
                make_monitor("ZeroMOD", x=3840, size=(1920, 480)),
            ]
        ),
        clock=FakeClock(),
        presence=FakePresence(),
        store=Store(tmp_path / "state.sqlite3"),
        config=config,
        service=None,  # type: ignore[arg-type]
    )
    environment.service = make_service(environment)
    return environment


def test_first_tick_shows_an_image_everywhere(env: Env) -> None:
    changes = env.service.tick()
    assert {c.playlist for c in changes} == {"konachan", "fantasy"}
    current = env.service.current(env.konachan)
    assert current is not None
    assert env.shown("AW3225QF") == env.shown("PLX2783H") == current  # mirrored
    assert env.shown("ZeroMOD") == env.tmp / "fantasy" / "f1.png"  # natural order


def test_changes_after_the_interval(env: Env) -> None:
    env.service.tick()
    first = env.service.current(env.konachan)
    assert not env.tick_for(INTERVAL - STEP)
    changes = env.tick_for(STEP)
    assert {c.reason for c in changes} == {"scheduled"}
    assert env.service.current(env.konachan) != first
    assert env.shown("ZeroMOD") == env.tmp / "fantasy" / "f2.png"


def test_shuffle_shows_every_image_once_per_cycle(env: Env) -> None:
    seen = []
    for _ in range(8):
        env.service.next(env.konachan)
        seen.append(env.service.current(env.konachan))
    assert len(set(seen)) == 8


@pytest.mark.parametrize("away", ["locked", "idle"])
def test_paused_while_away(env: Env, away: str) -> None:
    env.service.tick()
    env.tick_for(INTERVAL / 2)
    if away == "locked":
        env.presence.locked = True
    else:
        env.presence.idle = 301
    assert not env.tick_for(10 * INTERVAL)
    assert env.service.pause_reason() is not None
    env.presence.locked, env.presence.idle = False, 0
    # The count continues where it paused; it does not restart.
    assert not env.tick_for(INTERVAL / 2 - STEP)
    assert env.tick_for(STEP)


def test_presence_unknown_never_pauses(env: Env) -> None:
    env.presence.locked = env.presence.idle = None
    env.service.tick()
    assert env.tick_for(INTERVAL)


def test_sleep_is_not_counted(env: Env) -> None:
    env.service.tick()
    env.clock.advance(8 * 3600)  # PC asleep overnight
    assert not env.service.tick()


def test_slow_ticks_are_not_mistaken_for_sleep(env: Env) -> None:
    """`wallpaper run --poll 120`: two minutes between ticks still count."""
    env.service = make_service(env, tick_period=120)
    env.service.tick()
    env.clock.advance(120)
    assert {c.reason for c in env.service.tick()} == {"scheduled"}
    env.clock.advance(8 * 3600)  # but a night asleep still doesn't
    assert not env.service.tick()


def test_detached_group_is_postponed_without_losing_the_image(env: Env) -> None:
    env.service.tick()
    upcoming = env.service.status()[0].upcoming[0]
    env.backend.detach("AW3225QF")
    env.backend.detach("PLX2783H")
    assert not [c for c in env.tick_for(5 * INTERVAL) if c.playlist == "konachan"]
    env.backend.update("PLX2783H", rect=make_monitor("x").rect)
    back = [c for c in env.tick_for(INTERVAL - STEP) if c.playlist == "konachan"]
    assert [c.reason for c in back] == ["restored"]  # its current image first
    [change] = [c for c in env.tick_for(STEP) if c.playlist == "konachan"]
    assert change.image == upcoming  # the postponed image, not a skipped one
    assert change.shown_on == ("PLX2783H",)


def test_group_advances_while_one_member_is_attached(env: Env) -> None:
    env.service.tick()
    oled = env.backend.unlist("AW3225QF")  # switched off: gone from the list
    [change] = [c for c in env.tick_for(INTERVAL) if c.playlist == "konachan"]
    assert change.shown_on == ("PLX2783H",)
    old_image = env.shown("PLX2783H")

    env.backend.relist(replace(oled, wallpaper=None))
    [restored] = [c for c in env.service.tick() if c.playlist == "konachan"]
    assert restored.reason == "restored" and restored.shown_on == ("AW3225QF",)
    assert env.shown("AW3225QF") == old_image
    assert not env.service.tick()  # nothing more to do


def test_monitor_detaching_while_setting_postpones(env: Env) -> None:
    env.service.tick()
    before = env.service.current(env.konachan)
    env.tick_for(INTERVAL - STEP)

    snapshot = env.backend.monitors_calls + 1

    def switch_off(call: int) -> None:
        if call > snapshot:  # after the tick's snapshot, before the setter's check
            env.backend.detach("AW3225QF")
            env.backend.detach("PLX2783H")

    env.backend.before_monitors = switch_off
    env.clock.advance(STEP)
    assert not [c for c in env.service.tick() if c.playlist == "konachan"]
    assert env.service.current(env.konachan) == before  # nothing consumed


def test_next_and_previous_walk_history(env: Env) -> None:
    env.service.tick()
    images = [env.service.current(env.konachan)]
    for _ in range(3):
        images.append(env.service.next(env.konachan).image)
    back = [env.service.previous(env.konachan) for _ in range(2)]
    assert [c.image for c in back if c] == [images[2], images[1]]
    assert env.service.next(env.konachan).image == images[2]
    assert env.service.next(env.konachan).image == images[3]
    new = env.service.next(env.konachan).image
    assert new not in images


def test_previous_at_the_start(env: Env) -> None:
    assert env.service.previous(env.konachan) is None
    env.service.tick()
    assert env.service.previous(env.konachan) is None


def test_manual_change_restarts_the_interval(env: Env) -> None:
    env.service.tick()
    env.tick_for(INTERVAL - 2 * STEP)
    env.service.next(env.konachan)
    assert not [c for c in env.tick_for(INTERVAL - STEP) if c.playlist == "konachan"]


def test_next_with_no_monitor_attached_consumes_nothing(env: Env) -> None:
    env.service.tick()
    upcoming = env.service.status()[0].upcoming[0]
    env.backend.detach("AW3225QF")
    env.backend.detach("PLX2783H")
    with pytest.raises(MonitorNotAttachedError):
        env.service.next(env.konachan)
    assert env.service.status()[0].upcoming[0] == upcoming


def test_favourite_writes_the_txt_file(env: Env) -> None:
    env.service.tick()
    image, added = env.service.favourite(env.fantasy)
    assert added and image == env.tmp / "fantasy" / "f1.png"
    assert not env.service.favourite(env.fantasy)[1]
    text = env.config.favourites_file.read_text(encoding="utf-8")
    assert text == f"{image}\n"


def test_unfavourite_removes_only_the_current_image(env: Env) -> None:
    env.service.tick()
    env.config.favourites_file.parent.mkdir()
    env.config.favourites_file.write_text("# mine\nother.png\n", encoding="utf-8")
    image, _ = env.service.favourite(env.fantasy)
    assert env.service.unfavourite(env.fantasy) == (image, True)
    assert env.service.unfavourite(env.fantasy) == (image, False)
    text = env.config.favourites_file.read_text(encoding="utf-8")
    assert text == "# mine\nother.png\n"


def test_remove_lists_hides_and_moves_on(env: Env) -> None:
    env.service.tick()
    image, change = env.service.remove(env.konachan)
    assert change is not None and change.image != image
    assert image.exists()  # never deleted
    assert env.config.to_remove_file.read_text(encoding="utf-8") == f"{image}\n"
    assert env.service.previous(env.konachan) is None  # hidden from history
    shown = {env.service.next(env.konachan).image for _ in range(20)}
    assert image not in shown


def test_externally_listed_current_image_is_replaced(env: Env) -> None:
    env.service.tick()
    current = env.service.current(env.fantasy)
    env.config.to_remove_file.parent.mkdir(parents=True, exist_ok=True)
    env.config.to_remove_file.write_text(f"{current}\n", encoding="utf-8")
    [change] = [c for c in env.service.tick() if c.playlist == "fantasy"]
    assert change.image != current


def test_deleted_files_are_skipped(env: Env) -> None:
    env.service.tick()  # f1
    (env.tmp / "fantasy" / "f2.png").unlink()
    assert env.service.next(env.fantasy).image.name == "f10.png"


def test_new_files_join(env: Env) -> None:
    env.service.tick()  # f1
    make_images(env.tmp / "fantasy", "f1b.png")
    assert env.service.next(env.fantasy).image.name == "f1b.png"


def test_unreadable_images_are_skipped(env: Env) -> None:
    Image.new("RGB", (64, 16)).save(env.tmp / "fantasy" / "f2.png")
    env.config = replace(
        env.config,
        playlists=(env.konachan, replace(env.fantasy, mode=ImageMode.FILL)),
    )
    env.restart()
    env.service.tick()  # f1.png is an empty file, not an image
    assert env.service.current(env.fantasy) == env.tmp / "fantasy" / "f2.png"
    shown = env.shown("ZeroMOD")
    assert shown is not None and shown.parent == env.tmp / "rendered"


def test_state_survives_a_restart(env: Env) -> None:
    env.service.tick()
    env.tick_for(INTERVAL / 2)
    current = env.service.current(env.konachan)
    env.restart()
    env.service.restore()
    changes = env.service.tick()
    assert {c.reason for c in changes} == {"restored"}  # current image put back
    assert env.service.current(env.konachan) == current
    assert [c for c in env.tick_for(INTERVAL / 2) if c.playlist == "konachan"]


def test_find_playlist(env: Env) -> None:
    assert env.service.find_playlist("Konachan") is env.konachan
    assert env.service.find_playlist("zero") is env.fantasy  # part of a name
    with pytest.raises(PlaylistError, match="konachan, fantasy"):
        env.service.find_playlist(None)
    with pytest.raises(MonitorSelectionError):
        env.service.find_playlist("nope")


def test_status(env: Env) -> None:
    env.backend.detach("PLX2783H")
    env.service.tick()
    konachan, fantasy = env.service.status()
    assert [m.monitor.attached for m in konachan.members if m.monitor] == [
        True,
        False,
    ]
    assert fantasy.current == env.tmp / "fantasy" / "f1.png"
    assert [p.name for p in fantasy.upcoming] == ["f2.png", "f10.png", "f1.png"]
