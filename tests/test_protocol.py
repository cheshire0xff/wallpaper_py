"""The protocol's models and its published JSON Schema."""

import json
from pathlib import Path
from typing import Any, get_args

from wallpaper_py.protocol import (
    ERROR_KINDS,
    PROTOCOL,
    CommandRequest,
    Event,
    json_schema,
)

SCHEMA_FILE = Path(__file__).parents[1] / "docs" / "protocol.schema.json"


def test_published_schema_is_current() -> None:
    published = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert published == json_schema(), (
        "docs/protocol.schema.json is out of date; "
        "run `uv run python scripts/protocol_schema.py`"
    )


def mapping(schema: dict[str, Any], name: str) -> set[str]:
    """The tags of a discriminated union in the schema."""
    union = schema["$defs"][name]
    while "discriminator" not in union:  # Reply: anyOf[results, error]
        union = union["anyOf"][0]
    return set(union["discriminator"]["mapping"])


def test_every_command_has_a_result() -> None:
    schema = json_schema()
    request = schema["$defs"]["Envelope"]["properties"]["request"]
    commands = set(request["discriminator"]["mapping"]) - {"subscribe"}
    assert mapping(schema, "Reply") == commands


def test_every_request_says_whether_it_changes_anything() -> None:
    requests = get_args(CommandRequest)
    assert requests
    for model in requests:
        assert isinstance(model.changes, bool), model
    assert {m.__name__ for m in requests if not m.changes} == {
        "StatusRequest",
        "UpcomingRequest",
        "StatsRequest",
        "SettingsRequest",
    }


def test_events_and_error_kinds_are_published() -> None:
    schema = json_schema()
    assert mapping(schema, "Event") == {
        "changed",
        "overview_changed",
        "marks_changed",
        "show_page",
    }
    assert len(get_args(get_args(Event)[0])) == 4
    kinds = schema["$defs"]["ErrorInfo"]["properties"]["kind"]["enum"]
    assert tuple(kinds) == ERROR_KINDS
    assert str(PROTOCOL) in schema["description"]
