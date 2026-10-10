"""schema/contracts.json is generated from the models, and must match them.

It is the bridge to the TypeScript types (packages/contracts/ts), so a stale file means
the clients compile against a contract the services no longer speak.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from gridlock_contracts import messages

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_schema.py"


def _export_schema() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_schema", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema() -> dict[str, Any]:
    return cast(dict[str, Any], _export_schema().build_schema())


def test_the_committed_schema_matches_the_models() -> None:
    export = _export_schema()
    committed = Path(export.SCHEMA_PATH).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == export.render(), (
        "schema/contracts.json is stale. From the repo root run\n"
        "  uv run python -m packages.contracts.scripts.export_schema\n"
        "  npm run build -w packages/contracts/ts"
    )


def test_every_model_and_enum_is_emitted_once_by_name() -> None:
    assert sorted(_schema()["$defs"]) == sorted(
        [
            "Coordinates",
            "EvidenceChunk",
            "RetrievalResult",
            "QueueItem",
            *(model.__name__ for model in messages.PAYLOADS.values()),
            "Tier",
            "ReportState",
            "LocationConfidence",
        ]
    )


def test_every_object_refuses_unknown_fields_and_requires_all_of_its_own() -> None:
    for name, definition in _schema()["$defs"].items():
        if definition.get("type") != "object":
            continue
        assert definition["additionalProperties"] is False, name
        # Defaults included: the wire always carries the field, so a client may rely on it.
        assert sorted(definition["required"]) == sorted(definition["properties"]), name


def _paths_with_titles(node: object, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        mapping = cast(dict[str, object], node)
        if "title" in mapping:
            found.append(path)
        for key, value in mapping.items():
            found += _paths_with_titles(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(cast(list[object], node)):
            found += _paths_with_titles(value, f"{path}/{i}")
    return found


def test_no_field_carries_a_title_that_would_become_a_stray_type() -> None:
    defs = _schema()["$defs"]
    stray = [
        path
        for name, definition in defs.items()
        if "properties" in definition
        for path in _paths_with_titles(definition["properties"], name)
    ]
    assert stray == []


def test_the_schema_is_plain_json_with_stable_ordering() -> None:
    rendered = _export_schema().render()
    assert json.loads(rendered) == _schema()
    assert rendered.endswith("}\n")
