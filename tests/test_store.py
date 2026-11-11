from pathlib import Path

import pytest

from wallpaper_py.core.model import WallpaperError
from wallpaper_py.core.store import Store


@pytest.fixture(name="store")
def fixture_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state" / "state.sqlite3")


def test_state_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path) as store:
        first = store.append_history("p", Path("a.jpg"), 1.0)
        store.set_cursor("p", first)
        store.add_elapsed("p", 12.5)
    with Store(path) as store:
        state = store.playlist_state("p")
        assert state.cursor == first and state.elapsed == 12.5


def test_unknown_playlist_has_empty_state(store: Store) -> None:
    state = store.playlist_state("nope")
    assert state.cursor is None and state.elapsed == 0 and state.deck_pos == -1


def test_set_cursor_restarts_the_interval(store: Store) -> None:
    store.add_elapsed("p", 100)
    store.set_cursor("p", store.append_history("p", Path("a.jpg"), 1.0))
    assert store.playlist_state("p").elapsed == 0


def test_history_walks_per_playlist(store: Store) -> None:
    ids = [store.append_history("p", Path(f"{i}.jpg"), i) for i in range(4)]
    store.append_history("other", Path("x.jpg"), 9)
    assert [e.id for e in store.history_after("p", ids[1])] == ids[2:]
    assert [e.id for e in store.history_before("p", ids[2])] == [ids[1], ids[0]]
    latest = store.latest_history("p")
    assert latest is not None and latest.path == Path("3.jpg")


def test_transaction_rolls_back_on_error(store: Store) -> None:
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.append_history("p", Path("a.jpg"), 1.0)
            raise RuntimeError
    assert store.latest_history("p") is None


def test_deck(store: Store) -> None:
    store.new_deck("p", [Path("b.jpg"), Path("a.jpg")])
    store.insert_cards("p", [(0.5, Path("c.jpg"))])
    assert store.deck_paths("p") == {"a.jpg", "b.jpg", "c.jpg"}
    store.set_deck_pos("p", 0.0)
    assert store.deck_after("p", 0.0) == [(0.5, Path("c.jpg")), (1.0, Path("a.jpg"))]


def test_keep_applied_forgets_other_monitors(store: Store) -> None:
    store.set_applied("MON-A", "p", Path("a.jpg"), 1.0)
    store.set_applied("mon-b", "p", Path("a.jpg"), 1.0)
    store.keep_applied(["mon-a"])
    assert store.applied("mon-A") == Path("a.jpg")
    assert store.applied("mon-b") is None


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path) as store:
        # pylint: disable-next=protected-access
        store._db.execute("PRAGMA user_version = 99")
    with pytest.raises(WallpaperError, match="newer"):
        Store(path)
