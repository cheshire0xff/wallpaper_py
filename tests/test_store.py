from pathlib import Path

import pytest

from wallpaper_py.core.model import WallpaperError
from wallpaper_py.core.store import SCHEMA_VERSION, Store


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


@pytest.mark.parametrize("version", [1, 99])
def test_another_schema_is_refused_not_deleted(tmp_path: Path, version: int) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path) as store:
        store.append_history("p", Path("a.jpg"), 1.0)
        # pylint: disable-next=protected-access
        store._db.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(WallpaperError) as error:
        Store(path)
    message = str(error.value)
    assert "another version of wallpaper_py" in message
    assert f"delete {path} to start afresh" in message
    assert "only the history and shuffle state are lost" in message
    assert path.exists()  # never deleted for you


def test_new_database_has_the_current_schema(tmp_path: Path) -> None:
    with Store(tmp_path / "state.sqlite3") as store:
        # pylint: disable-next=protected-access
        (version,) = store._db.execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION == 2


def test_insert_history_after_an_entry(store: Store) -> None:
    a, b = (store.append_history("p", Path(f"{n}.jpg"), 1.0) for n in "ab")
    store.append_history("other", Path("x.jpg"), 1.0)
    picked = store.insert_history("p", a, Path("picked.jpg"), 2.0)
    assert [e.id for e in store.history_after("p", a)] == [picked, b]
    assert [e.id for e in store.history_before("p", b)] == [picked, a]
    end = store.insert_history("p", b, Path("end.jpg"), 3.0)  # after the last
    first = store.insert_history("p", None, Path("none.jpg"), 4.0)  # no cursor
    assert [e.id for e in store.history_before("p")] == [first, end, b, picked, a]
    latest = store.latest_history("p")
    assert latest is not None and latest.path == Path("none.jpg")


def test_many_inserts_at_one_place_keep_the_order(store: Store) -> None:
    a = store.append_history("p", Path("a.jpg"), 1.0)
    b = store.append_history("p", Path("b.jpg"), 1.0)
    inserted = [store.insert_history("p", a, Path(f"{i}.jpg"), 1.0) for i in range(80)]
    # Each goes right after `a`, so the newest comes first; the halved gaps
    # were renumbered on the way.
    assert [e.id for e in store.history_after("p", a)] == [*reversed(inserted), b]


def test_hold_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with Store(path) as store:
        assert not store.playlist_state("p").held
        assert store.set_held("p", True)
        assert not store.set_held("p", True)  # already held
    with Store(path) as store:
        assert store.playlist_state("p").held
        assert store.set_held("p", False)
        assert not store.set_held("p", False)
        assert not store.playlist_state("p").held
