"""ClassicAgentProvider: real Bedrock Agents Classic via bedrock-agent-runtime.

Consumes the entire boto3 EventStream (trace, chunk, returnControl, errors).
The stream is single-consumer: the caller drains it to completion in a
background task, persisting each event; the SSE endpoint tails the DB.

Return control: on a returnControl event we record the proposed function and
parameters. On resume, returnControlInvocationResults[] must echo the exact
invocationId, actionGroup, and function, and reuse the same sessionId, or a new
orchestration turn silently starts. We assert that correlation before grading.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Iterator

import boto3
from botocore.exceptions import ClientError, EventStreamError

from .base import InvokeRequest, InvokeResult, Provider, ProviderEvent

EVIDENCE = "LIVE_CLASSIC"


class ClassicAgentProvider:
    name = "classic-live"
    evidence_level = EVIDENCE
    banner = None

    def __init__(self, region: str, agent_id: str, alias_id: str, model: str | None = None):
        self.region = region
        self.agent_id = agent_id
        self.alias_id = alias_id
        self.model = model
        self._session = boto3.Session()
        self._rt = self._session.client("bedrock-agent-runtime", region_name=region)

    # -- fixture tool: hardcoded synthetic business data, kept local --
    @staticmethod
    def _fixture_tool_result(function: str, params: dict) -> str:
        if function == "lookup_demo_ticket":
            tid = params.get("ticket_id", "UNKNOWN")
            return json.dumps({"ticket_id": tid, "status": "OPEN",
                               "assignee": "demo-bot", "priority": "P3"})
        if function == "request_demo_report":
            return json.dumps({"report_id": "RPT-DEMO-001",
                               "title": params.get("report_title", "untitled"),
                               "state": "QUEUED"})
        return json.dumps({"result": "ok"})

    @staticmethod
    def _emit(ev) -> ProviderEvent:
        if "chunk" in ev:
            return ProviderEvent("chunk", {"text": ev["chunk"]["bytes"].decode("utf-8", "replace")})
        if "trace" in ev:
            return ProviderEvent("trace", {"trace": ev["trace"].get("trace", {})})
        if "returnControl" in ev:
            return ProviderEvent("returnControl", {"returnControl": ev["returnControl"]})
        return ProviderEvent("other", {"keys": list(ev.keys())})

    @staticmethod
    def _executed_code(events: list[ProviderEvent]) -> bool:
        """True iff some buffered event carries a real sandbox execution output."""
        for e in events:
            if e.type != "trace":
                continue
            orch = e.payload.get("trace", {}).get("orchestrationTrace", {})
            co = orch.get("observation", {}).get("codeInterpreterInvocationOutput")
            if co and co.get("executionOutput"):
                return True
        return False

    def invoke(self, req: InvokeRequest) -> Iterator[ProviderEvent]:
        """Stream one logical turn. When req.retry_until_code is set, buffer each
        attempt and re-invoke on a fresh session id until one actually executes
        code in the sandbox (or max_attempts is reached); only the kept attempt's
        events are streamed, so the UI shows exactly one coherent trace. A note
        event records how many attempts it took — honest, not hidden."""
        if req.retry_until_code:
            attempts = max(1, req.max_attempts)
            last: list[ProviderEvent] = []
            for i in range(attempts):
                attempt_req = replace(req, session_id=f"{req.session_id}-try{i}",
                                      retry_until_code=False)
                buf = list(self._invoke_turn(attempt_req))
                if self._executed_code(buf):
                    yield ProviderEvent("other", {"note": "code-interpreter injection landed",
                                                  "attempts": i + 1})
                    yield from buf
                    return
                last = buf
            yield ProviderEvent("other", {"note": "no sandbox execution within attempt budget "
                                                  "(agent narrated the tool call instead)",
                                          "attempts": attempts})
            yield from last
            return
        yield from self._invoke_turn(req)

    def _invoke_turn(self, req: InvokeRequest) -> Iterator[ProviderEvent]:
        """One logical turn. If the agent returns control AND the channel is
        local_tool_response, automatically issue the resume with the (optionally
        injected) tool observation and continue streaming — all events under one
        run, in order, so the injected string is visible reappearing in the next
        modelInvocationInput.text."""
        rc = None
        try:
            resp = self._rt.invoke_agent(
                agentId=self.agent_id, agentAliasId=self.alias_id,
                sessionId=req.session_id, inputText=req.input_text,
                enableTrace=req.enable_trace)
            for ev in resp["completion"]:
                pe = self._emit(ev)
                if pe.type == "returnControl":
                    rc = pe.payload["returnControl"]
                yield pe
        except (ClientError, EventStreamError) as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            msg = getattr(e, "response", {}).get("Error", {}).get("Message", str(e))
            yield ProviderEvent("failure", {"code": code, "message": msg[:500]})
            return

        # Return-control resume (indirect tool-result injection channel).
        if rc and req.channel == "local_tool_response":
            yield ProviderEvent("other", {"note": "resuming with tool observation",
                                          "injected": req.tool_result_override is not None})
            yield from self.resume_with_tool_result(req, rc, req.tool_result_override)

    def resume_with_tool_result(self, req: InvokeRequest, rc: dict,
                                injected: str | None) -> Iterator[ProviderEvent]:
        """Second InvokeAgent call supplying returnControlInvocationResults.
        Correlation is asserted by the caller before grading."""
        inv = rc.get("invocationInputs", [{}])[0].get("functionInvocationInput", {})
        invocation_id = rc.get("invocationId")
        func = inv.get("function")
        ag = inv.get("actionGroup")
        params = {p["name"]: p.get("value") for p in inv.get("parameters", [])}
        body = injected if injected is not None else self._fixture_tool_result(func, params)
        try:
            resp = self._rt.invoke_agent(
                agentId=self.agent_id, agentAliasId=self.alias_id,
                sessionId=req.session_id, enableTrace=req.enable_trace,
                sessionState={
                    "invocationId": invocation_id,
                    "returnControlInvocationResults": [{
                        "functionResult": {
                            "actionGroup": ag, "function": func,
                            "responseBody": {"TEXT": {"body": body}},
                        }
                    }],
                })
            for ev in resp["completion"]:
                yield self._emit(ev)
        except (ClientError, EventStreamError) as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            msg = getattr(e, "response", {}).get("Error", {}).get("Message", str(e))
            yield ProviderEvent("failure", {"code": code, "message": msg[:500]})

    def finalize(self, events: list[ProviderEvent], req: InvokeRequest) -> InvokeResult:
        prompt_parts, raw_parts, rationale_parts, final_parts = [], [], [], []
        reasoning_parts: list[str] = []
        ci_code_parts: list[str] = []
        ci_out_parts: list[str] = []
        rc = None
        failure = None
        for e in events:
            if e.type == "chunk":
                final_parts.append(e.payload.get("text", ""))
            elif e.type == "returnControl":
                rc = e.payload["returnControl"]
            elif e.type == "failure":
                failure = e.payload
            elif e.type == "trace":
                orch = e.payload["trace"].get("orchestrationTrace", {})
                mii = orch.get("modelInvocationInput", {})
                if mii.get("text"):
                    prompt_parts.append(mii["text"])
                mio = orch.get("modelInvocationOutput", {})
                raw = mio.get("rawResponse", {}).get("content")
                if raw:
                    raw_parts.append(raw)
                if mio.get("reasoningContent"):
                    reasoning_parts.append(json.dumps(mio["reasoningContent"]))
                rat = orch.get("rationale", {}).get("text")
                if rat:
                    rationale_parts.append(rat)
                # code interpreter: the code the model actually ran + its sandbox output
                ci = orch.get("invocationInput", {}).get("codeInterpreterInvocationInput")
                if ci and ci.get("code"):
                    ci_code_parts.append(ci["code"])
                co = orch.get("observation", {}).get("codeInterpreterInvocationOutput")
                if co and co.get("executionOutput"):
                    ci_out_parts.append(co["executionOutput"])

        prompt = "".join(prompt_parts) or None
        raw = "".join(raw_parts) or None
        rationale = "".join(rationale_parts) or None
        reasoning = "".join(reasoning_parts) or None
        final = "".join(final_parts) or None
        provenance = {
            "region": self.region, "agent_id": self.agent_id, "alias_id": self.alias_id,
            "model": self.model, "prompt_mode": "managed-default",
            "parser_mode": "managed-default", "sdk": f"boto3 {boto3.__version__}",
        }
        if failure:
            provenance["failure"] = failure
        if ci_code_parts or ci_out_parts:
            provenance["code_interpreter"] = {
                "code": "\n\n".join(ci_code_parts),
                "output": "\n".join(ci_out_parts),
            }
        return InvokeResult(
            provider=self.name, evidence_level=EVIDENCE, banner=None,
            submitted_input=req.input_text, model_prompt=prompt, raw_response=raw,
            rationale=rationale, reasoning=reasoning, final_answer=final,
            return_control=rc, events=[{"type": e.type, **e.payload} for e in events],
            provenance=provenance,
            field_presence={
                "model_prompt": prompt is not None, "raw_response": raw is not None,
                "rationale": rationale is not None, "reasoning": reasoning is not None,
                "final_answer": final is not None, "return_control": rc is not None,
            })


_: Provider  # structural check hint
