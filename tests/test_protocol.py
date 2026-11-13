"""The protocol's models and its published JSON Schema."""

import json
from pathlib import Path
from typing import Any, get_args

from wallpaper_py.protocol import (
    COMMANDS,
    ERROR_KINDS,
    PROTOCOL,
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
    assert mapping(schema, "Reply") == set(COMMANDS)
    request = schema["$defs"]["Envelope"]["properties"]["request"]
    assert set(request["discriminator"]["mapping"]) == {*COMMANDS, "subscribe"}


def test_events_and_error_kinds_are_published() -> None:
    schema = json_schema()
    assert mapping(schema, "Event") == {"changed", "overview_changed"}
    assert len(get_args(get_args(Event)[0])) == 2
    kinds = schema["$defs"]["ErrorInfo"]["properties"]["kind"]["enum"]
    assert tuple(kinds) == ERROR_KINDS
    assert str(PROTOCOL) in schema["description"]
