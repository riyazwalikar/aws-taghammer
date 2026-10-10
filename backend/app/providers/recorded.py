"""RecordedTraceProvider: replay a captured real Classic trace for conference
replay. Always badged RECORDED_CLASSIC with the captured date + source metadata.
Never presents replayed evidence as a fresh live call.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

from .base import InvokeRequest, InvokeResult, ProviderEvent

EVIDENCE = "RECORDED_CLASSIC"


class RecordedTraceProvider:
    name = "recorded-live"
    evidence_level = EVIDENCE

    def __init__(self, fixture_path: str):
        self.fixture_path = fixture_path
        with open(fixture_path) as f:
            self.fixture = json.load(f)
        prov = self.fixture.get("provenance", {})
        self.banner = (f"RECORDED CLASSIC — replay of a real trace captured "
                       f"{prov.get('captured_utc', 'unknown')} "
                       f"(agent {prov.get('agent_id', '?')}). Not a live call.")

    def invoke(self, req: InvokeRequest) -> Iterator[ProviderEvent]:
        for ev in self.fixture.get("events", []):
            t = ev.get("type", "other")
            yield ProviderEvent(t, {k: v for k, v in ev.items() if k != "type"})

    def finalize(self, events: list[ProviderEvent], req: InvokeRequest) -> InvokeResult:
        f = self.fixture
        prov = dict(f.get("provenance", {}))
        prov["replayed_from"] = os.path.basename(self.fixture_path)
        prov["prompt_mode"] = prov.get("prompt_mode", "managed-default")
        prov["parser_mode"] = prov.get("parser_mode", "managed-default")
        raw = f.get("raw_model_response")
        prompt = f.get("model_prompt")
        rationale = f.get("rationale")
        final = f.get("final_answer")
        return InvokeResult(
            provider=self.name, evidence_level=EVIDENCE, banner=self.banner,
            submitted_input=f.get("submitted_input", req.input_text),
            model_prompt=prompt, raw_response=raw, rationale=rationale,
            reasoning=None, final_answer=final, return_control=None,
            events=[{"type": e.type, **e.payload} for e in events],
            provenance=prov,
            field_presence={"model_prompt": bool(prompt), "raw_response": bool(raw),
                            "rationale": bool(rationale), "final_answer": bool(final)})
