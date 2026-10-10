#!/usr/bin/env python3
"""
Capture a real Classic InvokeAgent trace (spec gate (a): "capture a real trace"),
with bounded retry on the transient dependencyFailedException Nova sometimes
returns, then report the two make-or-break signals:

  1. modelInvocationOutput.rawResponse.content populated & non-empty
  2. baseline tag census over that raw output

Saves the full ordered event stream to fixtures/<ts>_<label>.json with provenance
so it can later be replayed with a `recorded-live` badge. This is the seed of the
fixture recorder deliverable.

Usage:
    AWS_PROFILE=myiamadmin python3 capture_trace.py --region us-east-1 \
        --agent-id ID --alias-id ID [--text "..."] [--label baseline] [--retries 6]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

TAG_NAMES = ["thinking", "answer", "function_calls", "invoke", "parameters",
             "final_response"]
FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def census(text: str) -> dict:
    out = {}
    for t in TAG_NAMES:
        opens = re.findall(rf"<\s*{re.escape(t)}\b[^>]*?(?<!/)>", text, re.IGNORECASE)
        closes = re.findall(rf"<\s*/\s*{re.escape(t)}\s*>", text, re.IGNORECASE)
        selfc = re.findall(rf"<\s*{re.escape(t)}\b[^>]*?/>", text, re.IGNORECASE)
        if opens or closes or selfc:
            out[t] = {"open": len(opens), "close": len(closes), "self": len(selfc)}
    return out


def invoke_once(rt, agent_id, alias_id, text, session_id):
    events = []
    raw_parts, prompt_parts, final_parts, rationale_parts = [], [], [], []
    resp = rt.invoke_agent(
        agentId=agent_id, agentAliasId=alias_id, sessionId=session_id,
        inputText=text, enableTrace=True,
    )
    for ev in resp["completion"]:
        # Normalize each event to JSON-safe form (bytes -> decoded text).
        rec = {}
        if "chunk" in ev:
            b = ev["chunk"]["bytes"].decode("utf-8", "replace")
            final_parts.append(b)
            rec = {"type": "chunk", "text": b}
        elif "trace" in ev:
            t = ev["trace"].get("trace", {})
            rec = {"type": "trace", "trace": t}
            orch = t.get("orchestrationTrace", {})
            mii = orch.get("modelInvocationInput", {})
            if mii.get("text"):
                prompt_parts.append(mii["text"])
            mio = orch.get("modelInvocationOutput", {})
            raw = mio.get("rawResponse", {}).get("content")
            if raw:
                raw_parts.append(raw)
            rat = orch.get("rationale", {}).get("text")
            if rat:
                rationale_parts.append(rat)
        elif "returnControl" in ev:
            rec = {"type": "returnControl", "returnControl": ev["returnControl"]}
        else:
            rec = {"type": "other", "keys": list(ev.keys())}
        events.append(rec)
    return {
        "events": events,
        "raw": "".join(raw_parts),
        "prompt": "".join(prompt_parts),
        "final": "".join(final_parts),
        "rationale": "".join(rationale_parts),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--alias-id", required=True)
    ap.add_argument("--text", default="Look up the status of demo ticket ABC-123.")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--retries", type=int, default=6)
    args = ap.parse_args()

    session = boto3.Session()
    acct = session.client("sts").get_caller_identity()["Account"]
    rt = session.client("bedrock-agent-runtime", region_name=args.region)
    session_id = f"capture-{uuid.uuid4()}"

    result = None
    for attempt in range(1, args.retries + 1):
        try:
            eprint(f"[invoke] attempt {attempt}/{args.retries} session={session_id}")
            result = invoke_once(rt, args.agent_id, args.alias_id, args.text, session_id)
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"].get("Message", "")
            eprint(f"[invoke] {code}: {msg[:120]}")
            if code in ("dependencyFailedException", "throttlingException",
                        "ThrottlingException", "internalServerException"):
                back = min(2 ** attempt, 30)
                eprint(f"[invoke] transient — backoff {back}s")
                time.sleep(back)
                session_id = f"capture-{uuid.uuid4()}"  # fresh session per retry
                continue
            raise
    if result is None:
        eprint("[invoke] exhausted retries")
        return 1

    tags = census(result["raw"])
    os.makedirs(FIX_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    fixture = {
        "schema_version": 1,
        "provider": "classic-live",
        "provenance": {
            "account_suffix": acct[-4:], "region": args.region,
            "agent_id": args.agent_id, "alias_id": args.alias_id,
            "session_id": session_id, "input_text": args.text,
            "sdk": f"boto3 {boto3.__version__}",
            "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "prompt_mode": "managed-default", "parser_mode": "managed-default",
        },
        "submitted_input": args.text,
        "model_prompt": result["prompt"],
        "raw_model_response": result["raw"],
        "rationale": result["rationale"],
        "final_answer": result["final"],
        "tag_census_raw": tags,
        "events": result["events"],
    }
    path = os.path.join(FIX_DIR, f"{ts}_{args.label}.json")
    json.dump(fixture, open(path, "w"), indent=2)

    print("=== capture summary ===")
    print(f"fixture: {os.path.relpath(path)}")
    print(f"events: {len(result['events'])}  final chars: {len(result['final'])}")
    rawlen = len(result["raw"])
    if rawlen:
        print(f"PASS rawResponse.content populated: {rawlen} bytes  (raw-tag exhibit VIABLE)")
    else:
        print("FAIL rawResponse.content EMPTY — raw-tag exhibit degrades to inference")
    if tags:
        print("baseline tag census (raw output):")
        for k, v in tags.items():
            print(f"  <{k}>: open={v['open']} close={v['close']} self={v['self']}")
    else:
        print("WARN no narrative control tags in raw output for this model")
    print(f"\nfinal answer (first 300): {result['final'][:300]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
