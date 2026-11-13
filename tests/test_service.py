"""PlaylistService with a fake backend, clock and presence."""

# pylint: disable=redefined-outer-name

import logging
import random
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core.config import Config, Order, PlaylistConfig
from wallpaper_py.core.library import Library
from wallpaper_py.core.marks import MarkFileBusyError
from wallpaper_py.core.model import (
    ImageLoadError,
    ImageMode,
    MonitorId,
    MonitorNotAttachedError,
)
from wallpaper_py.core.selection import MonitorSelectionError
from wallpaper_py.core.service import (
    Change,
    ManualPlaylistError,
    PlaylistError,
    PlaylistService,
)
from wallpaper_py.core.store import Store

from .fakes import (
    BusyFile,
    FakeBackend,
    FakeClock,
    FakePresence,
    make_images,
    make_monitor,
    fake_id,
)

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
                "1",
                tmp_path / "konachan",
                (MonitorId(fake_id("AW3225QF")), MonitorId(fake_id("PLX2783H"))),
                INTERVAL,
                Order.SHUFFLE,
            ),
            PlaylistConfig(
                "fantasy",
                "2",
                tmp_path / "fantasy",
                (MonitorId(fake_id("ZeroMOD")),),
                INTERVAL,
                Order.SORTED,
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
    # Windows spells an id in different cases in different APIs.
    assert env.service.find_playlist(fake_id("ZeroMOD").lower()) is env.fantasy
    with pytest.raises(PlaylistError, match="konachan, fantasy"):
        env.service.find_playlist(None)
    with pytest.raises(MonitorSelectionError):
        env.service.find_playlist("nope")


def test_no_playlists_is_a_service_that_rotates_nothing(env: Env) -> None:
    env.config = replace(env.config, playlists=())
    env.restart()
    assert not env.tick_for(INTERVAL) and not env.backend.set_calls
    assert not env.service.status()
    with pytest.raises(PlaylistError, match="No playlists yet"):
        env.service.find_playlist(None)
    with pytest.raises(PlaylistError, match="not in any playlist"):
        env.service.find_playlist("AW3225QF")


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


# -- pick ----------------------------------------------------------------------


def real_image(path: Path) -> Path:
    """A small real image (pick checks that Pillow can open it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), "teal").save(path)
    return path


def test_pick_from_another_directory_and_back(env: Env) -> None:
    env.service.tick()
    before = env.service.current(env.konachan)
    upcoming = env.service.status()[0].upcoming
    env.tick_for(INTERVAL - 2 * STEP)
    picked = real_image(env.tmp / "elsewhere" / "x.png")
    change = env.service.pick(env.konachan, picked)
    assert (change.image, change.reason) == (picked, "picked")
    assert change.shown_on == ("AW3225QF", "PLX2783H")
    assert env.shown("AW3225QF") == picked
    assert env.service.status()[0].upcoming == upcoming  # the deck is untouched
    # The interval starts again.
    assert not [c for c in env.tick_for(INTERVAL - STEP) if c.playlist == "konachan"]

    back = env.service.previous(env.konachan)
    assert back is not None and back.image == before
    assert env.service.next(env.konachan).image == picked  # forward in history
    assert env.service.next(env.konachan).image == upcoming[0]


def test_pick_goes_right_after_the_cursor(env: Env) -> None:
    env.service.tick()
    images = [env.service.current(env.konachan)]
    images += [env.service.next(env.konachan).image for _ in range(2)]
    env.service.previous(env.konachan)  # back on images[1]
    picked = real_image(env.tmp / "elsewhere" / "x.png")
    env.service.pick(env.konachan, picked)
    assert env.service.next(env.konachan).image == images[2]  # what was ahead
    walked_back = []
    while (change := env.service.previous(env.konachan)) is not None:
        walked_back.append(change.image)
    assert walked_back == [picked, images[1], images[0]]


def test_pick_from_the_directory_counts_as_seen(env: Env) -> None:
    for image in (env.tmp / "konachan").iterdir():
        real_image(image)
    env.service.tick()
    first = env.service.current(env.konachan)
    later = env.service.status(upcoming=8)[0].upcoming[-1]  # last of the cycle
    # Spelled differently (here with "..") it is still the same image.
    typed = env.tmp / "konachan" / ".." / "konachan" / later.name
    assert env.service.pick(env.konachan, typed).image == later
    upcoming = env.service.status(upcoming=8)[0].upcoming
    assert later not in upcoming and len(upcoming) == 6
    rest = [env.service.next(env.konachan).image for _ in range(6)]
    assert len({first, later, *rest}) == 8  # the whole cycle, once each


def test_pick_in_sorted_order(env: Env) -> None:
    for image in (env.tmp / "fantasy").iterdir():
        real_image(image)
    env.service.tick()  # f1
    env.service.pick(env.fantasy, real_image(env.tmp / "elsewhere" / "x.png"))
    assert env.service.next(env.fantasy).image.name == "f2.png"  # a detour
    env.service.pick(env.fantasy, env.tmp / "fantasy" / "f10.png")
    assert env.service.next(env.fantasy).image.name == "f1.png"  # moved the walk


def test_pick_refuses_missing_and_unreadable_images(env: Env) -> None:
    env.service.tick()
    current = env.service.current(env.konachan)
    calls = len(env.backend.set_calls)
    with pytest.raises(ImageLoadError, match="No such image file"):
        env.service.pick(env.konachan, env.tmp / "nowhere.png")
    with pytest.raises(ImageLoadError, match="Cannot open"):
        env.service.pick(env.konachan, env.tmp / "konachan" / "k01.jpg")  # empty
    with pytest.raises(ImageLoadError, match="No such image file"):
        env.service.pick(env.konachan, env.tmp / "konachan")  # a directory
    assert env.service.current(env.konachan) == current
    assert len(env.backend.set_calls) == calls


def test_pick_refuses_images_listed_for_removal(env: Env) -> None:
    env.service.tick()
    picked = real_image(env.tmp / "elsewhere" / "x.png")
    env.service.to_remove.add(picked)
    with pytest.raises(PlaylistError, match="listed for removal"):
        env.service.pick(env.konachan, picked)


def test_pick_with_no_monitor_attached_changes_nothing(env: Env) -> None:
    env.service.tick()
    current = env.service.current(env.konachan)
    env.backend.detach("AW3225QF")
    env.backend.detach("PLX2783H")
    with pytest.raises(MonitorNotAttachedError):
        env.service.pick(env.konachan, real_image(env.tmp / "elsewhere" / "x.png"))
    assert env.service.current(env.konachan) == current
    assert len(env.store.history_before("1")) == 1  # nothing was added


# -- hold and manual -------------------------------------------------------------


def test_held_playlist_waits_but_takes_commands(env: Env) -> None:
    env.service.tick()
    env.tick_for(INTERVAL / 2)
    assert env.service.hold(env.konachan)
    assert not env.service.hold(env.konachan)  # already held: no error
    assert env.service.held(env.konachan)
    assert not [c for c in env.tick_for(10 * INTERVAL) if c.playlist == "konachan"]
    assert {c.playlist for c in env.tick_for(INTERVAL)} == {"fantasy"}

    # Commands still work.
    shown = env.service.next(env.konachan).image
    assert env.service.previous(env.konachan) is not None
    picked = real_image(env.tmp / "elsewhere" / "x.png")
    assert env.service.pick(env.konachan, picked).image == picked
    assert shown != picked


def test_resume_continues_the_count(env: Env) -> None:
    env.service.tick()
    env.tick_for(INTERVAL / 2)
    env.service.hold(env.konachan)
    env.tick_for(10 * INTERVAL)
    assert env.service.resume(env.konachan)
    assert not env.service.resume(env.konachan)  # not held: no error
    waiting = env.tick_for(INTERVAL / 2 - STEP)
    assert not [c for c in waiting if c.playlist == "konachan"]
    assert [c for c in env.tick_for(STEP) if c.playlist == "konachan"]


def test_hold_survives_a_restart(env: Env) -> None:
    env.service.tick()
    current = env.service.current(env.konachan)
    env.service.hold(env.konachan)
    env.restart()
    env.service.restore()
    assert env.service.held(env.konachan)
    changes = [c for c in env.tick_for(5 * INTERVAL) if c.playlist == "konachan"]
    assert [c.reason for c in changes] == ["restored"]  # its image put back
    assert env.service.current(env.konachan) == current
    assert env.service.status()[0].held


def test_held_playlist_still_replaces_a_removed_image(env: Env) -> None:
    env.service.tick()
    env.service.hold(env.konachan)
    env.backend.detach("AW3225QF")
    env.backend.detach("PLX2783H")
    image, change = env.service.remove(env.konachan)
    assert change is None  # nowhere to show the next one yet
    env.backend.update("PLX2783H", rect=make_monitor("x").rect)
    [change] = [c for c in env.service.tick() if c.playlist == "konachan"]
    assert change.image != image and change.reason == "scheduled"


def test_manual_playlist_never_changes_by_itself(env: Env) -> None:
    manual = replace(env.fantasy, interval=None)
    env.config = replace(env.config, playlists=(env.konachan, manual))
    env.restart()
    assert manual.manual
    assert not [c for c in env.tick_for(10 * INTERVAL) if c.playlist == "fantasy"]
    assert env.service.current(manual) is None  # not even a first image
    assert env.service.next(manual).image.name == "f1.png"
    assert not [c for c in env.tick_for(10 * INTERVAL) if c.playlist == "fantasy"]
    assert env.service.next(manual).image.name == "f2.png"
    assert env.service.previous(manual) is not None

    with pytest.raises(ManualPlaylistError, match="nothing to hold"):
        env.service.hold(manual)
    with pytest.raises(ManualPlaylistError, match="nothing to resume"):
        env.service.resume(manual)
    assert not env.service.held(manual)


def test_two_monitors_of_one_model_are_told_apart(env: Env) -> None:
    """Same name, other id: only the one in the config gets its images."""
    env.service.tick()
    shown = len(env.backend.set_calls)
    env.backend.relist(make_monitor("ZeroMOD", x=9000, pnp="OTHER"))
    env.tick_for(INTERVAL)
    targets = {c.monitor_id for c in env.backend.set_calls[shown:]}
    assert MonitorId(fake_id("ZeroMOD")) in targets
    assert MonitorId(fake_id("ZeroMOD", pnp="OTHER")) not in targets


def test_a_monitor_on_another_port_waits_to_be_assigned(env: Env) -> None:
    """Windows gives it a new id: the playlist waits, nothing else gets it."""
    env.service.tick()
    old = env.backend.unlist("ZeroMOD")
    env.backend.relist(replace(old, rect=None))  # the old port, detached
    env.backend.relist(make_monitor("ZeroMOD", x=3840, pnp="ZEROMOD-DP2"))
    shown = len(env.backend.set_calls)
    env.tick_for(INTERVAL)
    assert all(
        "ZEROMOD" not in str(c.monitor_id) for c in env.backend.set_calls[shown:]
    )
    with pytest.raises(MonitorNotAttachedError):
        env.service.next(env.fantasy)
    [_, fantasy] = env.service.status()
    [member] = fantasy.members
    assert member.monitor is not None and not member.monitor.attached


def test_a_monitor_id_in_another_case_is_the_same_monitor(env: Env) -> None:
    aw = fake_id("AW3225QF")
    konachan = replace(env.konachan, monitors=(MonitorId(aw.lower()), MonitorId(aw)))
    env.config = replace(env.config, playlists=(konachan, env.fantasy))
    env.restart()
    [change] = [c for c in env.service.tick() if c.playlist == "konachan"]
    assert change.shown_on == ("AW3225QF",)  # once
    assert env.service.next(konachan).shown_on == ("AW3225QF",)
    assert env.service.find_playlist("AW3225QF") == konachan
    # The backend gets the id as the desktop spells it.
    assert {str(c.monitor_id) for c in env.backend.set_calls} >= {aw}


def test_state_of_a_playlist_no_longer_in_the_config_goes_on_request(
    env: Env,
) -> None:
    konachan, fantasy = env.config.playlists
    env.service.tick()
    env.service.hold(fantasy)
    shown = env.service.current(fantasy)
    env.config = replace(env.config, playlists=(konachan,))  # fantasy taken out
    env.restart()
    # Loading drops nothing: back in the config, it carries on.
    env.config = replace(env.config, playlists=(konachan, fantasy))
    env.restart()
    assert env.service.current(fantasy) == shown
    assert env.service.held(fantasy)

    env.config = replace(env.config, playlists=(konachan,))
    env.restart()
    assert env.service.prune_state() == ["2"]  # fantasy, only
    assert env.service.current(env.konachan) is not None  # in the config: kept
    assert not env.store.history_before("2")
    assert not env.store.playlist_state("2").held
    assert not env.service.prune_state()


def test_prune_refuses_without_a_config_file(env: Env) -> None:
    """An empty config from no file must not wipe every playlist's state."""
    env.service.tick()
    env.config = replace(env.config, playlists=(), from_file=False)
    env.restart()
    with pytest.raises(PlaylistError, match="no config file"):
        env.service.prune_state()
    assert env.store.history_before("1") and env.store.history_before("2")


def test_a_renamed_playlist_keeps_its_state(env: Env) -> None:
    """The state goes by the config's id: a new name changes nothing."""
    env.service.tick()
    env.service.hold(env.konachan)
    current = env.service.current(env.konachan)
    renamed = replace(env.konachan, name="anime")
    env.config = replace(env.config, playlists=(renamed, env.fantasy))
    env.restart()
    assert env.service.current(renamed) == current
    assert env.service.held(renamed)
    assert not env.service.prune_state()


def test_a_to_remove_list_never_read_stops_choosing(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown is not empty: an image listed for removal must not be shown."""
    env.service.tick()
    listed = env.tmp / "lists" / "to-remove.txt"
    listed.parent.mkdir()
    listed.write_text(f"{env.tmp / 'fantasy' / 'f2.png'}\n", encoding="utf-8")
    busy = BusyFile(listed, monkeypatch)
    env.restart()  # a new process: it has never read the list
    calls = len(env.backend.set_calls)

    caplog.set_level(logging.WARNING)
    assert not env.tick_for(2 * INTERVAL)  # due, but nothing is chosen
    assert len(env.backend.set_calls) == calls
    unknown = [r for r in caplog.records if "until the to-remove list" in r.message]
    assert len(unknown) == 1  # logged once, retried every tick
    for command in (env.service.next, env.service.previous):
        with pytest.raises(MarkFileBusyError, match="until the to-remove list"):
            command(env.fantasy)
    with pytest.raises(MarkFileBusyError, match="until the to-remove list"):
        env.service.pick(env.fantasy, env.tmp / "fantasy" / "f10.png")
    [_, fantasy] = env.service.status()
    assert fantasy.upcoming == ()  # it would list f2.png
    assert len(env.backend.set_calls) == calls

    busy.busy = False  # readable: rotation goes on, past f2.png
    # The time it waited counted: overdue, it changes on the very next tick.
    env.clock.advance(STEP)
    changes = [c for c in env.service.tick() if c.playlist == "fantasy"]
    assert changes[0].image.name == "f10.png"
