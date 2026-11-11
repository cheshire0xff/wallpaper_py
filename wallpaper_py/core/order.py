"""
Which new image a playlist shows next: a sorted walk or a shuffled deck.

Both only *peek*: nothing is consumed until the caller has actually shown the
image (see PlaylistService), so a postponed change never loses an image.
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from .library import natural_key
from .store import Store

Valid = Callable[[Path], bool]


def sorted_from(images: Sequence[Path], after: Path | None) -> Iterator[Path]:
    """
    `images` (naturally sorted) starting after `after`, wrapping around.

    Stateless apart from `after`, so files added to or removed from the
    directory simply take or leave their place in the order.
    """
    start = 0
    if after is not None:
        start = bisect.bisect_right(images, natural_key(after), key=natural_key)
    yield from images[start:]
    yield from images[:start]


def next_sorted(
    images: Sequence[Path], after: Path | None, valid: Valid
) -> Path | None:
    return next((p for p in sorted_from(images, after) if valid(p)), None)


def upcoming_sorted(
    images: Sequence[Path], after: Path | None, valid: Valid, limit: int
) -> list[Path]:
    return list(islice((p for p in sorted_from(images, after) if valid(p)), limit))


@dataclass(frozen=True)
class Card:
    position: float
    path: Path


class ShuffleDeck:
    """
    A persisted shuffled deck: every image once before any repeats, so the
    rotation doubles as a review of the whole library. The cards not drawn
    yet are the *upcoming* queue.

    New files are shuffled into the undrawn part at uniformly random places;
    missing, removed or unreadable files are skipped (the `valid` check).
    When the deck runs out, a new cycle is shuffled from the current files.
    """

    def __init__(self, store: Store, playlist: str, rng: random.Random) -> None:
        self._store = store
        self._playlist = playlist
        self._rng = rng

    def peek(
        self, images: Sequence[Path], valid: Valid, avoid: Path | None = None
    ) -> Card | None:
        """
        The next card to show. `avoid` (the image shown now) is not put first
        in a new cycle, so the cycle boundary does not repeat an image.
        """
        if not images:
            # Directory missing or not mounted yet: keep the deck as it is.
            return None
        self._add_new(images)
        card = next(self._valid_cards(valid), None)
        if card is None:  # this cycle is done
            self._store.new_deck(self._playlist, self._shuffled(images, avoid))
            card = next(self._valid_cards(valid), None)
        return card

    def upcoming(self, images: Sequence[Path], valid: Valid, limit: int) -> list[Card]:
        """The rest of this cycle (no further: the next one isn't shuffled yet)."""
        if images:
            self._add_new(images)
        return list(islice(self._valid_cards(valid), limit))

    def draw(self, card: Card) -> None:
        """Consume everything up to and including `card`."""
        self._store.set_deck_pos(self._playlist, card.position)

    def _valid_cards(self, valid: Valid) -> Iterator[Card]:
        position = self._store.playlist_state(self._playlist).deck_pos
        for pos, path in self._store.deck_after(self._playlist, position):
            if valid(path):
                yield Card(pos, path)

    def _shuffled(self, images: Sequence[Path], avoid: Path | None) -> list[Path]:
        cards = list(images)
        self._rng.shuffle(cards)
        if avoid is not None and len(cards) > 1 and cards[0] == avoid:
            swap = self._rng.randrange(1, len(cards))
            cards[0], cards[swap] = cards[swap], cards[0]
        return cards

    def _add_new(self, images: Sequence[Path]) -> None:
        in_deck = self._store.deck_paths(self._playlist)
        if not in_deck:
            self._store.new_deck(self._playlist, self._shuffled(images, None))
            return
        new = [p for p in images if str(p) not in in_deck]
        if not new:
            return
        drawn_up_to = self._store.playlist_state(self._playlist).deck_pos
        remaining = [
            pos for pos, _ in self._store.deck_after(self._playlist, drawn_up_to)
        ]
        cards: list[tuple[float, Path]] = []
        for path in new:
            # Each of the len(remaining) + 1 gaps is equally likely, which is
            # exactly a uniformly random insertion.
            gap = self._rng.randint(0, len(remaining))
            low = remaining[gap - 1] if gap > 0 else drawn_up_to
            high = remaining[gap] if gap < len(remaining) else low + 1
            position = (low + high) / 2
            bisect.insort(remaining, position)
            cards.append((position, path))
        self._store.insert_cards(self._playlist, cards)
