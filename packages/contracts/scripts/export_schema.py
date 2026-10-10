"""Write packages/contracts/schema/contracts.json from the Pydantic models.

    uv run python -m packages.contracts.scripts.export_schema     # from the repo root

The JSON Schema is the bridge to TypeScript: packages/contracts/ts compiles it to
generated.d.ts. The Pydantic models stay the single source of truth -- neither file is
edited by hand, and the contracts test suite fails if this one is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from gridlock_contracts import messages
from gridlock_contracts.models import Coordinates, EvidenceChunk, QueueItem, RetrievalResult

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "contracts.json"

# Every model T010 ships, in the order it appears in domain-model section 6. The three
# enums need no entry: each is reachable from a model, so it is emitted once, by name.
MODELS: list[type[BaseModel]] = [
    Coordinates,
    EvidenceChunk,
    RetrievalResult,
    QueueItem,
    *messages.PAYLOADS.values(),
]


def _drop_field_titles(node: Any, *, keep: bool = False) -> Any:
    """Remove the per-field "title"s Pydantic adds ("Report Id"), keeping each model's.

    A titled field makes the TypeScript compiler emit a named alias for it
    (`type ReportId = string`), dozens of names nobody imports, some colliding across
    models.
    """
    if isinstance(node, dict):
        mapping = cast(dict[str, Any], node)
        out: dict[str, Any] = {}
        for key, value in mapping.items():
            if key == "title" and not keep:
                continue
            if key == "$defs":
                defs = cast(dict[str, Any], value)
                out[key] = {name: _drop_field_titles(d, keep=True) for name, d in defs.items()}
            else:
                out[key] = _drop_field_titles(value)
        return out
    if isinstance(node, list):
        return [_drop_field_titles(item) for item in cast(list[Any], node)]
    return node


def build_schema() -> dict[str, Any]:
    """One schema whose $defs hold every model and enum, as clients receive them."""
    # Serialization mode: the shape on the wire, which is what a client reads.
    _, schema = models_json_schema(
        [(model, "serialization") for model in MODELS],
        ref_template="#/$defs/{model}",
    )
    defs = schema["$defs"]
    return _drop_field_titles(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "GridLockContract",
            "description": "Any GridLock contract payload.",
            "anyOf": [{"$ref": f"#/$defs/{model.__name__}"} for model in MODELS],
            "$defs": dict(sorted(defs.items())),
        },
        keep=True,
    )


def render() -> str:
    return json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(render(), encoding="utf-8", newline="\n")
    cwd = Path.cwd()
    shown = SCHEMA_PATH.relative_to(cwd) if SCHEMA_PATH.is_relative_to(cwd) else SCHEMA_PATH
    print(f"wrote {shown}")


if __name__ == "__main__":
    main()
