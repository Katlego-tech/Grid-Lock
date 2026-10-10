"""Load a triage prompt file and compute its version (docs/design/triage.md section 6.2).

`prompt_version` is `"<file stem>:<first 12 hex chars of SHA-256 of the file's raw bytes>"`.
Raw bytes, no normalisation: any change, whitespace and line endings included, is a new
version, so results stay comparable across prompt revisions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "triage_v1.prompt"


@dataclass(frozen=True)
class LoadedPrompt:
    template: str  # a LangChain f-string template
    version: str


def load_prompt(path: Path = DEFAULT_PROMPT) -> LoadedPrompt:
    """Read the prompt once (at startup) and version it by its exact bytes."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return LoadedPrompt(template=raw.decode("utf-8"), version=f"{path.stem}:{digest}")
