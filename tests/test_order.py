import random
from pathlib import Path

import pytest

from wallpaper_py.core.order import (
    ShuffleDeck,
    next_sorted,
    sorted_from,
    upcoming_sorted,
)
from wallpaper_py.core.store import Store

IMAGES = [Path(f"img{i}.jpg") for i in range(1, 11)]  # natural order


def always(_: Path) -> bool:
    return True


class TestSorted:
    def test_starts_at_the_beginning(self) -> None:
        assert next_sorted(IMAGES, None, always) == IMAGES[0]

    def test_continues_after_the_last_image_and_wraps(self) -> None:
        assert next_sorted(IMAGES, IMAGES[1], always) == IMAGES[2]
        assert next_sorted(IMAGES, IMAGES[-1], always) == IMAGES[0]

    def test_last_image_may_be_gone_from_the_directory(self) -> None:
        assert next_sorted(IMAGES, Path("img5b.jpg"), always) == Path("img6.jpg")

    def test_skips_invalid_images(self) -> None:
        def valid(path: Path) -> bool:
            return path != IMAGES[3]

        assert next_sorted(IMAGES, IMAGES[2], valid) == IMAGES[4]

    def test_nothing_valid(self) -> None:
        assert next_sorted(IMAGES, None, lambda _: False) is None

    def test_upcoming(self) -> None:
        assert upcoming_sorted(IMAGES, IMAGES[8], always, 3) == [
            IMAGES[9],
            IMAGES[0],
            IMAGES[1],
        ]

    def test_single_image_repeats(self) -> None:
        assert list(sorted_from(IMAGES[:1], IMAGES[0])) == IMAGES[:1]


@pytest.fixture(name="store")
def fixture_store() -> Store:
    return Store(":memory:")


def draw_all(deck: ShuffleDeck, images: list[Path], count: int) -> list[Path]:
    drawn = []
    for _ in range(count):
        card = deck.peek(images, always)
        assert card is not None
        deck.draw(card)
        drawn.append(card.path)
    return drawn


class TestShuffleDeck:
    def test_every_image_once_per_cycle(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        first = draw_all(deck, IMAGES, 10)
        second = draw_all(deck, IMAGES, 10)
        assert sorted(first) == sorted(IMAGES) == sorted(second)
        assert first != second

    def test_peek_does_not_consume(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        assert deck.peek(IMAGES, always) == deck.peek(IMAGES, always)

    def test_deck_persists(self, tmp_path: Path) -> None:
        with Store(tmp_path / "s.db") as store:
            deck = ShuffleDeck(store, "1", random.Random(1))
            first = draw_all(deck, IMAGES, 4)
        with Store(tmp_path / "s.db") as store:
            deck = ShuffleDeck(store, "1", random.Random(2))  # other seed
            rest = draw_all(deck, IMAGES, 6)
        assert sorted(first + rest) == sorted(IMAGES)

    def test_new_files_join_the_current_cycle(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        drawn = draw_all(deck, IMAGES, 5)
        new = [Path("new1.jpg"), Path("new2.jpg")]
        rest = draw_all(deck, IMAGES + new, 7)
        assert sorted(drawn + rest) == sorted(IMAGES + new)

    def test_new_files_land_anywhere_in_the_rest(self, store: Store) -> None:
        """Uniform insertion: over many trials, every slot gets used."""
        slots = set()
        for seed in range(200):
            store.new_deck("1", IMAGES[:3])
            deck = ShuffleDeck(store, "1", random.Random(seed))
            order = [
                c.path for c in deck.upcoming(IMAGES[:3] + [Path("n.jpg")], always, 9)
            ]
            slots.add(order.index(Path("n.jpg")))
        assert slots == {0, 1, 2, 3}

    def test_skips_invalid_cards(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        removed = set(IMAGES[:5])
        cards = deck.upcoming(IMAGES, lambda p: p not in removed, 20)
        assert {c.path for c in cards} == set(IMAGES[5:])

    def test_missing_directory_keeps_the_deck(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        draw_all(deck, IMAGES, 3)
        assert deck.peek([], always) is None
        assert len(store.deck_paths("1")) == 10
        assert len(draw_all(deck, IMAGES, 7)) == 7

    def test_new_cycle_does_not_repeat_the_last_image(self, store: Store) -> None:
        for seed in range(30):
            deck = ShuffleDeck(store, str(seed), random.Random(seed))
            last = draw_all(deck, IMAGES[:3], 3)[-1]
            card = deck.peek(IMAGES[:3], always, avoid=last)
            assert card is not None and card.path != last

    def test_nothing_valid(self, store: Store) -> None:
        deck = ShuffleDeck(store, "1", random.Random(1))
        assert deck.peek(IMAGES, lambda _: False) is None
