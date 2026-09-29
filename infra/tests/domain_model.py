"""Read the rules straight out of docs/design/domain-model.md.

The tests compare the database against the document itself, not against a hand-copied
list of fields. If someone edits the document and not the migration, or the migration
and not the document, a test fails -- which is the point: "a field that is not here does
not exist".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DOMAIN_MODEL = Path(__file__).resolve().parents[2] / "docs" / "design" / "domain-model.md"


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # the diagram's type with Optional~...~ removed, e.g. "UUID", "List~EvidenceChunk~"
    optional: bool


def _text(path: Path = DOMAIN_MODEL) -> str:
    return path.read_text(encoding="utf-8")


def _mermaid_block(kind: str) -> str:
    for block in re.findall(r"```mermaid\n(.*?)```", _text(), flags=re.DOTALL):
        if block.lstrip().startswith(kind):
            return block
    raise AssertionError(f"domain-model.md has no mermaid {kind} block")


@cache
def classes() -> dict[str, list[Field]]:
    """Class name -> fields, from the section 3 class diagram."""
    result: dict[str, list[Field]] = {}
    block = _mermaid_block("classDiagram")
    for name, body in re.findall(r"class (\w+) \{(.*?)\}", block, flags=re.DOTALL):
        fields: list[Field] = []
        for line in body.strip().splitlines():
            m = re.fullmatch(r"\s*\+(\S+)\s+(\w+)\s*", line)
            assert m, f"unparseable field line in class {name}: {line!r}"
            raw_type, field_name = m.groups()
            opt = re.fullmatch(r"Optional~(.+)~", raw_type)
            fields.append(Field(field_name, opt.group(1) if opt else raw_type, bool(opt)))
        result[name] = fields
    assert result, "no classes parsed from the class diagram"
    return result


@cache
def report_transitions() -> frozenset[tuple[str, str]]:
    """(from, to) pairs drawn in the section 5 state diagram, excluding [*]."""
    pairs = re.findall(r"^\s*(\w+)\s*-->\s*(\w+)", _mermaid_block("stateDiagram"), flags=re.M)
    assert pairs, "no transitions parsed from the state diagram"
    return frozenset(pairs)


@cache
def python_enums() -> dict[str, list[str]]:
    """Enum name -> values, from the section 6 contracts code block."""
    code = re.search(r"```python\n# packages/contracts/.*?enums\.py\n(.*?)```", _text(), re.DOTALL)
    assert code, "domain-model.md has no enums.py block in section 6"
    enums: dict[str, list[str]] = {}
    for name, body in re.findall(
        r"class (\w+)\(str, Enum\):\n(.*?)(?=\nclass |\Z)", code.group(1), re.DOTALL
    ):
        enums[name] = re.findall(r'\w+\s*=\s*"(\w+)"', body)
    return enums


@cache
def incident_states() -> list[str]:
    """Incident.state values, from the line under the section 5 state diagram."""
    line = re.search(r"^`Incident\.state` is .*$", _text(), flags=re.M)
    assert line, "domain-model.md does not state Incident.state's values"
    return re.findall(r"`([A-Z_]+)`", line.group(0))
