"""MonitorId: one monitor, however an API spells its id."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from wallpaper_py.core.model import MonitorId

DEVICE_PATH = r"\\?\DISPLAY#DELA246#5&2b3e4f&0&UID4352#{E6F07B5F-EE97}"


def test_equal_and_hashed_case_insensitively() -> None:
    upper, lower = MonitorId(DEVICE_PATH), MonitorId(DEVICE_PATH.lower())
    assert upper == lower and hash(upper) == hash(lower)
    assert lower in {upper} and {upper: 1}[lower] == 1
    assert upper != MonitorId(DEVICE_PATH.replace("4352", "4353"))


def test_never_equal_to_a_plain_string() -> None:
    """A str is not an id: comparing the two is a mistake, not a match."""
    assert MonitorId("A") != "A"


def test_keeps_its_spelling() -> None:
    lower = MonitorId(DEVICE_PATH.lower())
    assert str(MonitorId(DEVICE_PATH)) == DEVICE_PATH  # for the backend
    assert str(lower) == DEVICE_PATH.lower()
    assert f"{lower}" == DEVICE_PATH.lower()
    assert repr(lower) == f"MonitorId({DEVICE_PATH.lower()!r})"
    assert lower.key == MonitorId(DEVICE_PATH).key == DEVICE_PATH.casefold()


def test_surrounding_whitespace_is_not_part_of_the_id() -> None:
    """The one place ids are normalized: every reader of a config agrees."""
    padded = MonitorId(f"  {DEVICE_PATH}\t")
    assert str(padded) == DEVICE_PATH and padded == MonitorId(DEVICE_PATH)
    assert _Model.model_validate({"id": f" {DEVICE_PATH} "}).id.text == DEVICE_PATH


def test_frozen() -> None:
    monitor_id = MonitorId("A")
    with pytest.raises(AttributeError):
        monitor_id.text = "B"  # type: ignore[misc]


class _Model(BaseModel):
    id: MonitorId
    ids: tuple[MonitorId, ...] = ()


def test_pydantic_reads_and_writes_a_plain_string() -> None:
    model = _Model.model_validate_json(json.dumps({"id": DEVICE_PATH, "ids": ["b"]}))
    assert model.id == MonitorId(DEVICE_PATH.upper())
    assert str(model.id) == DEVICE_PATH  # spelling kept
    assert json.loads(model.model_dump_json()) == {"id": DEVICE_PATH, "ids": ["b"]}
    assert model.model_dump() == {"id": DEVICE_PATH, "ids": ("b",)}
    assert _Model.model_validate_json(model.model_dump_json()) == model
    # In Python, an id is taken as it is, or made from a string.
    assert _Model(id=MonitorId("a")).id == MonitorId("A")
    assert _Model.model_validate({"id": "a"}).id == MonitorId("A")
    assert _Model.model_json_schema()["properties"]["id"]["type"] == "string"


@pytest.mark.parametrize("value", [3, True, None, ["a"]])
def test_pydantic_refuses_anything_but_a_string(value: object) -> None:
    with pytest.raises(ValidationError):
        _Model.model_validate({"id": value})
