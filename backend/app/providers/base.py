"""Provider interface: invoke(request) -> ordered events + final result.

Three providers, never conflated:
  ClassicAgentProvider  -> bedrock-agent-runtime (InvokeAgent stream)  [classic-live]
  NovaLabProvider       -> bedrock-runtime (Converse) + local parser   [nova-lab]
  RecordedTraceProvider -> replay a captured fixture                    [recorded-live]

Each yields normalized events (dicts with a 'type') in stream order, and the
caller persists them. Evidence level and provider are carried on every event
bundle so no view can misattribute a local reproduction as managed behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol


@dataclass
class InvokeRequest:
    input_text: str
    session_id: str
    channel: str = "direct_user"          # direct_user | local_tool_response | real_kb
    tool_result_override: str | None = None  # injected content for return-control
    enable_trace: bool = True
    # classic-live only: the code-interpreter injection fires at a low per-call
    # rate (most turns narrate the tool call as text instead of executing). When
    # set, re-invoke on a fresh session until a turn produces a real
    # codeInterpreterInvocationOutput.executionOutput, then stream that turn.
    retry_until_code: bool = False
    max_attempts: int = 8


@dataclass
class ProviderEvent:
    type: str                              # trace | chunk | returnControl | failure | other
    payload: dict = field(default_factory=dict)


@dataclass
class InvokeResult:
    provider: str
    evidence_level: str                    # LIVE_CLASSIC | RECORDED_CLASSIC | NOVA_LAB
    banner: str | None
    submitted_input: str
    model_prompt: str | None               # modelInvocationInput.text (Classic) or local payload
    raw_response: str | None               # rawResponse.content (Classic) or model bytes
    rationale: str | None
    reasoning: str | None                  # only if AWS returned it
    final_answer: str | None
    return_control: dict | None
    events: list[dict]
    provenance: dict
    field_presence: dict                   # name -> bool (for "not provided by AWS")


class Provider(Protocol):
    name: str
    evidence_level: str
    banner: str | None

    def invoke(self, req: InvokeRequest) -> Iterator[ProviderEvent]:
        """Yield events in stream order. Drain fully even on error (append a
        failure event) so a dropped consumer never loses an in-progress turn."""
        ...

    def finalize(self, events: list[ProviderEvent], req: InvokeRequest) -> InvokeResult:
        """Assemble the evidence bundle from the drained events."""
        ...
