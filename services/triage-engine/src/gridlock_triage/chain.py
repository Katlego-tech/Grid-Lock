"""The triage chain: a report and its evidence in, one tier and a reason out.

docs/design/triage.md section 6.1. Built from LangChain primitives behind the TriageChain
protocol -- the portability boundary of US6 -- and imports only LangChain, Pydantic, the
standard library and gridlock_contracts. No transport or storage library may enter its
import graph (tests/test_chain_isolation.py).

The model is injected; this module never chooses one (T035 does, on the seed corpus).
Anything the model returns that is not exactly a valid TriageChainOutput raises
InvalidModelOutput with the raw text: a tier is never coerced, repaired or guessed.
"""

from __future__ import annotations

from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field, ValidationError

from gridlock_contracts.enums import Tier
from gridlock_contracts.models import EvidenceChunk
from gridlock_triage.prompt_loader import LoadedPrompt

# What the template shows for an absent value. Section 6.1 fixes it for evidence; the
# landmark and the category hint read the same way, so "none" always means "not given".
NONE = "none"


class TriageChainInput(BaseModel):
    description: str = Field(..., min_length=1, max_length=2000)
    reported_landmark: str | None = None
    category_hint: str | None = None
    evidence: list[EvidenceChunk] = []


class TriageChainOutput(BaseModel):
    tier: Tier
    reason: str = Field(..., min_length=5, max_length=500)


class InvalidModelOutput(Exception):
    """The model's reply did not parse into a TriageChainOutput.

    `raw` is the reply exactly as received. str(error) is the designed failure_reason
    (triage.md section 6.4): "invalid model output: <raw output>".
    """

    def __init__(self, raw: str) -> None:
        super().__init__(f"invalid model output: {raw}")
        self.raw = raw


class TriageChain(Protocol):
    model_id: str  # the exact model identifier sent to the provider; persisted as-is
    prompt_version: str  # section 6.2

    async def atriage(self, data: TriageChainInput) -> TriageChainOutput:
        """Raises InvalidModelOutput(raw: str) when the output does not parse."""
        ...


def _format_evidence(evidence: list[EvidenceChunk]) -> str:
    if not evidence:
        return NONE
    return "\n".join(f"- {chunk.text} (similarity {chunk.similarity:.2f})" for chunk in evidence)


def _variables(data: TriageChainInput) -> dict[str, str]:
    return {
        "description": data.description,
        "reported_landmark": data.reported_landmark if data.reported_landmark is not None else NONE,
        "category_hint": data.category_hint if data.category_hint is not None else NONE,
        "evidence": _format_evidence(data.evidence),
    }


def _template(prompt: LoadedPrompt) -> PromptTemplate:
    return PromptTemplate.from_template(prompt.template, template_format="f-string")


def render_prompt(prompt: LoadedPrompt, data: TriageChainInput) -> str:
    """The exact text the model is sent for `data`."""
    return _template(prompt).format(**_variables(data))


def parse_output(raw: str) -> TriageChainOutput:
    """The model's reply as a TriageChainOutput, or InvalidModelOutput(raw).

    Strict JSON only: no fence stripping, no case folding, no nearest-tier mapping.
    """
    try:
        return TriageChainOutput.model_validate_json(raw, strict=True)
    except ValidationError:
        raise InvalidModelOutput(raw) from None


class LangChainTriageChain:
    """prompt | model | text, then strict parsing. Works with any LangChain chat model."""

    def __init__(self, *, model: BaseChatModel, model_id: str, prompt: LoadedPrompt) -> None:
        self.model_id = model_id
        self.prompt_version = prompt.version
        self._runnable = _template(prompt) | model | StrOutputParser()

    async def atriage(self, data: TriageChainInput) -> TriageChainOutput:
        raw: str = await self._runnable.ainvoke(_variables(data))
        return parse_output(raw)
