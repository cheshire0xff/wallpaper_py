#!/usr/bin/env python3
"""
Write docs/protocol.schema.json from the protocol models.

    python scripts/protocol_schema.py

tests/test_protocol.py fails while the published file is out of date.
"""

import json
import sys
from pathlib import Path

from wallpaper_py.protocol import json_schema

SCHEMA_FILE = Path(__file__).parents[1] / "docs" / "protocol.schema.json"


def render() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SCHEMA_FILE.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {SCHEMA_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
