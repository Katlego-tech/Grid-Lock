"""The triage chain against docs/design/triage.md sections 6.1-6.3 and 9 (items 2, 3, 4, 11).

The model is a stub: these tests prove the plumbing -- the prompt renders, a valid answer
becomes a TriageChainOutput, and anything else raises InvalidModelOutput carrying the raw
text. Whether a real model picks the right tier is measured on the seed corpus (T035).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import Field

from gridlock_contracts.enums import Tier
from gridlock_contracts.models import EvidenceChunk
from gridlock_triage.chain import (
    InvalidModelOutput,
    LangChainTriageChain,
    TriageChain,
    TriageChainInput,
    TriageChainOutput,
    render_prompt,
)
from gridlock_triage.prompt_loader import DEFAULT_PROMPT, LoadedPrompt, load_prompt

PROMPT = load_prompt()

# triage.md section 6.3, verbatim.
WORKED_EXAMPLES = [
    ("men with a gun forcing my back door, I'm inside with my kids", Tier.CRITICAL_DISPATCH),
    ("men breaking through my back gate, 14 Sisulu Street", Tier.CRITICAL_DISPATCH),
    ("someone is breaking into the empty house next door, owners are overseas", Tier.URGENT),
    ("guys climbing into the spaza shop roof right now", Tier.URGENT),
    ("my car window was smashed overnight, radio gone", Tier.ADVISORY),
    ("streetlight out on the corner since Tuesday", Tier.MONITOR),
]


class RecordingModel(FakeListChatModel):
    """A stub chat model that also keeps the prompts it was sent."""

    seen: list[str] = Field(default_factory=list[str])

    def _call(self, messages: Sequence[Any], *args: Any, **kwargs: Any) -> str:
        self.seen.append("".join(str(m.content) for m in messages))
        return super()._call(messages, *args, **kwargs)


def _chain(*responses: str) -> tuple[LangChainTriageChain, RecordingModel]:
    model = RecordingModel(responses=list(responses))
    return LangChainTriageChain(model=model, model_id="stub-model-1", prompt=PROMPT), model


def _run(chain: TriageChain, data: TriageChainInput) -> TriageChainOutput:
    return asyncio.run(chain.atriage(data))


def _answer(tier: str, reason: str = "The reporter says it is happening right now.") -> str:
    return f'{{"tier": "{tier}", "reason": "{reason}"}}'


# --- the prompt -----------------------------------------------------------------------------


def test_the_prompt_file_is_the_template_in_the_design_doc() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "design" / "triage.md").read_text(
        encoding="utf-8"
    )
    block = re.search(r"on the first call\):\n\n```text\n(.*?)```", doc, re.DOTALL)
    assert block is not None, "triage.md section 6.2 no longer holds the template"
    assert DEFAULT_PROMPT.read_bytes() == block.group(1).encode("utf-8")


def test_the_template_renders_with_every_variable_set() -> None:
    evidence = [
        EvidenceChunk(
            landmark_id=uuid.uuid4(),
            text="Spar Vilakazi (Vilakazi Spar) — supermarket, Vilakazi Street, Orlando West",
            similarity=0.8126,
        ),
        EvidenceChunk(
            landmark_id=uuid.uuid4(), text="Vilakazi Street Precinct, Orlando West", similarity=0.7
        ),
    ]
    text = render_prompt(
        PROMPT,
        TriageChainInput(
            description="break-in at the Spar on Vilakazi",
            reported_landmark="the Spar",
            category_hint="burglary",
            evidence=evidence,
        ),
    )
    assert "Report: break-in at the Spar on Vilakazi\n" in text
    assert "Landmark the reporter named: the Spar\n" in text
    assert "Category hint: burglary\n" in text
    assert (
        "Landmark evidence:\n"
        "- Spar Vilakazi (Vilakazi Spar) — supermarket, Vilakazi Street, Orlando West"
        " (similarity 0.81)\n"
        "- Vilakazi Street Precinct, Orlando West (similarity 0.70)\n"
    ) in text


def test_the_template_renders_with_every_optional_variable_none() -> None:
    text = render_prompt(PROMPT, TriageChainInput(description="streetlight out on the corner"))
    assert "Landmark the reporter named: none\n" in text
    assert "Category hint: none\n" in text
    assert "Landmark evidence:\nnone\n" in text


def test_the_json_example_survives_with_single_braces() -> None:
    text = render_prompt(PROMPT, TriageChainInput(description="x"))
    assert '{"tier": "CRITICAL_DISPATCH" | "URGENT" | "ADVISORY" | "MONITOR", "reason":' in text
    assert "{{" not in text and "}}" not in text


def test_braces_in_a_report_reach_the_model_verbatim() -> None:
    """The description is a value, never part of the template: '{x}' must not raise or vanish."""
    text = render_prompt(PROMPT, TriageChainInput(description="they wrote {gang} on the wall"))
    assert "Report: they wrote {gang} on the wall\n" in text


def test_the_description_reaches_the_model_unchanged() -> None:
    raw = "  Two men  jumped the wall at no. 12\n— white Polo, no plates  "
    chain, model = _chain(_answer("URGENT"))
    _run(chain, TriageChainInput(description=raw))
    assert f"Report: {raw}\n" in model.seen[0]


# --- valid answers ------------------------------------------------------------------------


@pytest.mark.parametrize(("description", "tier"), WORKED_EXAMPLES)
def test_each_worked_example_yields_its_tier(description: str, tier: Tier) -> None:
    chain, model = _chain(_answer(tier.value))
    result = _run(chain, TriageChainInput(description=description))
    assert result == TriageChainOutput(tier=tier, reason=result.reason)
    assert result.tier is tier
    assert description in model.seen[0]


def test_the_chain_carries_its_model_id_and_prompt_version() -> None:
    chain, _ = _chain(_answer("MONITOR"))
    assert chain.model_id == "stub-model-1"
    assert chain.prompt_version == PROMPT.version


# --- no coercion: triage.md invariant 1 ---------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        _answer("HIGH"),
        _answer("CRITICAL"),
        _answer("URGENT!"),
        _answer("urgent"),
        "URGENT",
        "I think this is urgent because a door is being forced.",
        '```json\n{"tier": "URGENT", "reason": "A door is being forced right now."}\n```',
        '{"tier": "URGENT"}',
        '{"tier": "URGENT", "reason": "No"}',
        '{"tier": null, "reason": "The report is too vague to rank."}',
        "",
    ],
    ids=[
        "HIGH",
        "CRITICAL",
        "URGENT!",
        "lowercase",
        "bare-tier",
        "prose",
        "code-fenced",
        "no-reason",
        "short-reason",
        "null-tier",
        "empty",
    ],
)
def test_anything_but_a_valid_answer_raises_with_the_raw_output(raw: str) -> None:
    chain, _ = _chain(raw)
    with pytest.raises(InvalidModelOutput) as caught:
        _run(chain, TriageChainInput(description="someone is at the gate"))
    assert caught.value.raw == raw


def test_the_failure_reason_text_is_the_designed_one() -> None:
    """triage.md section 6.4: `invalid model output: <raw output>`."""
    assert str(InvalidModelOutput('{"tier": "HIGH"}')) == 'invalid model output: {"tier": "HIGH"}'


# --- prompt_version: triage.md section 6.2 ------------------------------------------------


def test_prompt_version_is_the_stem_and_12_hex_of_the_raw_bytes() -> None:
    assert re.fullmatch(r"triage_v1:[0-9a-f]{12}", PROMPT.version)


def test_one_changed_byte_changes_the_version(tmp_path: Path) -> None:
    original = tmp_path / "triage_v1.prompt"
    original.write_bytes(DEFAULT_PROMPT.read_bytes())
    edited = tmp_path / "edited" / "triage_v1.prompt"
    edited.parent.mkdir()
    edited.write_bytes(DEFAULT_PROMPT.read_bytes().replace(b"EXACTLY", b"EXACTLy", 1))

    assert load_prompt(original).version == PROMPT.version
    assert load_prompt(edited).version != PROMPT.version


def test_line_endings_count_as_a_change(tmp_path: Path) -> None:
    """No normalisation: a CRLF checkout would be a different prompt, hence .gitattributes."""
    crlf = tmp_path / "triage_v1.prompt"
    crlf.write_bytes(DEFAULT_PROMPT.read_bytes().replace(b"\n", b"\r\n"))
    assert load_prompt(crlf).version != PROMPT.version


def test_a_loaded_prompt_is_immutable() -> None:
    with pytest.raises(AttributeError):
        PROMPT.version = "triage_v1:000000000000"  # type: ignore[misc]
    assert isinstance(PROMPT, LoadedPrompt)
