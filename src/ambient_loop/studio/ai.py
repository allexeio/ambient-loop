"""Claude integration for the walkthrough (steps 1, 3, 5 and 6).

Claude is used where judgement on unstructured input helps, and never to produce engineering
numbers:
  - read the engineer's description, area schedules and BRUKL reports, and propose zones,
    areas, occupancy and annual demands (step 1/2), with the source of each value
  - review the rule-based terminal proposal against the description and explain it (step 3)
  - draft narrative for reports from the computed results it is given (steps 5/6)
Every proposal is shown to the engineer for confirmation before it is used. Without an API key
the app falls back to rules and forms. Calls use structured outputs validated by Pydantic, the
server-side refusal fallback, and adaptive thinking at medium effort.
Data note: project text and uploaded documents are sent to the Anthropic API.
"""

from __future__ import annotations

import base64
import os
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

MODEL = os.environ.get("MILIEU_CLAUDE_MODEL", "claude-opus-5-5")
BETAS = ["server-side-fallback-2026-07-01"]


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


SpaceTypeId = Literal["hotel_bedroom", "hotel_public", "residential", "office", "retail", "other"]


class ZoneProposal(BaseModel):
    name: str
    space_type: SpaceTypeId
    area_m2: float = Field(description="Gross internal area of the zone in m²")
    occupants: float = Field(description="Design occupants or beds; 0 if unknown")
    heating_kWh: float = Field(description="Annual space heating demand, kWh/yr; 0 if not given")
    cooling_kWh: float = Field(description="Annual space cooling demand (energy supplied to the space), kWh/yr; 0 if not given")
    dhw_kWh: float = Field(description="Annual DHW demand incl. circulation losses, kWh/yr; 0 if not given")
    source: str = Field(description="Where each value came from: the document and table, or 'estimated'")


class BuildingProposal(BaseModel):
    name: str
    location: str
    zones: list[ZoneProposal]
    fresh_air: Literal["included", "separate", "estimate", "unknown"] = Field(
        description="Whether the heating/cooling demands include ventilation (fresh-air) load")
    observations: list[str] = Field(description="Short engineering observations about the inputs")
    questions: list[str] = Field(description="Questions the engineer should answer before analysis")


SYSTEM_BRIEF = (
    "You are a senior building services engineer at Milieu, a London MEP consultancy, helping set up an energy "
    "strategy study. Extract the building description into zones. Use only figures stated in the text or documents; "
    "where a demand is not stated, return 0 and say so in 'source' (the app estimates it later from benchmarks). "
    "BRUKL reports give energy demand in MJ/m² or kWh/m² per HVAC system and zone area: convert to kWh/yr using the "
    "zone area (1 MJ = 0.2778 kWh). Heating and cooling demand in BRUKL is the demand delivered to the space, not fuel. "
    "Map spaces to the closest space type. Raise questions for anything ambiguous, especially whether fresh-air load "
    "is in the demands, operating hours and which areas are cooled."
)


def propose_building(description: str, documents: list[tuple[str, bytes]]) -> BuildingProposal:
    content: list[dict[str, object]] = []
    for name, data in documents:
        if name.lower().endswith(".pdf"):
            content.append({"type": "document", "title": name,
                            "source": {"type": "base64", "media_type": "application/pdf",
                                       "data": base64.standard_b64encode(data).decode()}})
        else:
            content.append({"type": "text", "text": f"<document name='{name}'>\n{data.decode(errors='replace')[:200000]}\n</document>"})
    content.append({"type": "text", "text": f"Engineer's description:\n{description}"})
    resp = _client().beta.messages.parse(
        model=MODEL, max_tokens=16000, betas=BETAS, fallbacks="default",
        output_config={"effort": "medium"}, system=SYSTEM_BRIEF,
        messages=[{"role": "user", "content": content}], output_format=BuildingProposal,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Claude could not process this input; enter the zones manually.")
    return resp.parsed_output


class TerminalReview(BaseModel):
    zone: str
    agree: bool
    suggested_terminal: str
    rationale: str


class TerminalReviewSet(BaseModel):
    reviews: list[TerminalReview]
    overall: str


def review_terminals(description: str, proposal: list[dict[str, object]], catalogue: dict[str, str]) -> TerminalReviewSet:
    msg = (f"Building description:\n{description}\n\nRule-based terminal proposal (zone, terminal id, temperatures):\n{proposal}\n\n"
           f"Terminal catalogue (id: label):\n{catalogue}\n\nReview each zone's terminal for a UK commercial project. Agree, or "
           "suggest a catalogue id with a one-sentence engineering rationale (comfort, simultaneous heating/cooling, "
           "heat-pump flow temperatures, fresh-air strategy, space). Keep each rationale under 40 words.")
    resp = _client().beta.messages.parse(
        model=MODEL, max_tokens=8000, betas=BETAS, fallbacks="default", output_config={"effort": "medium"},
        system="You are a senior building services engineer reviewing terminal unit choices.",
        messages=[{"role": "user", "content": msg}], output_format=TerminalReviewSet,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Terminal review unavailable.")
    return resp.parsed_output


class Narrative(BaseModel):
    summary: str = Field(description="3-5 sentence executive summary using only the numbers provided")
    key_points: list[str]
    risks: list[str]


def narrate(kind: str, facts: str) -> Narrative:
    resp = _client().beta.messages.parse(
        model=MODEL, max_tokens=8000, betas=BETAS, fallbacks="default", output_config={"effort": "medium"},
        system=("You write concise engineering narrative for Milieu reports in British English. Use ONLY the figures in "
                "the facts provided; never invent or recalculate numbers. Plain, precise, no marketing language."),
        messages=[{"role": "user", "content": f"Write the {kind}.\n\nFacts:\n{facts}"}], output_format=Narrative,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Narrative unavailable.")
    return resp.parsed_output
