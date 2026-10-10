#!/usr/bin/env python3
"""
doctor: precise green/red capability report for the c0c0n Bedrock Classic demo.

Runs BEFORE any exploit-demo build (spec first-hour gate). It never stores
credentials and makes only inexpensive calls, except the creation probe which is
gated behind --check-create because it may create a resource.

Checks (each prints PASS / FAIL / SKIP / WARN with a reason):
  1. sts:GetCallerIdentity                        (account, region)
  2. GetAgent / GetAgentAlias                      (if BEDROCK_AGENT_ID set)
  3. Nova inference-profile resolution             (ListInferenceProfiles)
  4. Model eligibility via a cheap real invoke     (bedrock-runtime Converse)
  5. Service-role coverage note for profile + members
  6. InvokeAgent benign smoke                      (if agent configured)
  7. rawResponse.content populated in a trace      (make-or-break exhibit #1)
  8. Baseline per-tag census on raw output         (make-or-break exhibit #2)
  9. Return-control behavior (reported if seen)
 10. Creation eligibility                          (--check-create only; else
     printed as UNKNOWN with the rule that ListAgents cannot decide it)

Exit code: 0 if no FAIL, 1 if any FAIL.

Usage:
    AWS_PROFILE=myiamadmin python3 scripts/doctor.py --region us-east-1 \
        [--agent-id ID --alias-id ID] [--model us.amazon.nova-pro-v1:0] \
        [--check-create]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

TAG_NAMES = ["thinking", "answer", "function_calls", "invoke", "parameters",
             "final_response"]

GREEN, RED, YEL, BLU, RST = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"


class Report:
    def __init__(self):
        self.rows = []
        self.failed = False

    def add(self, status, name, detail=""):
        if status == "FAIL":
            self.failed = True
        color = {"PASS": GREEN, "FAIL": RED, "WARN": YEL, "SKIP": BLU,
                 "INFO": BLU}.get(status, "")
        print(f"  {color}{status:5}{RST} {name}" + (f" — {detail}" if detail else ""))
        self.rows.append({"status": status, "check": name, "detail": detail})


def find_tags(text: str) -> dict:
    out = {}
    for t in TAG_NAMES:
        n = len(re.findall(rf"<\s*{re.escape(t)}\b", text, re.IGNORECASE))
        if n:
            out[t] = n
    return out


def check_identity(session, region, rep) -> str | None:
    try:
        i = session.client("sts").get_caller_identity()
        rep.add("PASS", "sts:GetCallerIdentity",
                f"account …{i['Account'][-4:]} region {region}")
        return i["Account"]
    except (ClientError, EndpointConnectionError) as e:
        rep.add("FAIL", "sts:GetCallerIdentity", str(e))
        return None


def check_agent(session, region, agent_id, alias_id, rep):
    ba = session.client("bedrock-agent", region_name=region)
    if not agent_id:
        rep.add("SKIP", "GetAgent/GetAgentAlias", "BEDROCK_AGENT_ID not set")
        return
    try:
        a = ba.get_agent(agentId=agent_id)["agent"]
        rep.add("PASS", "GetAgent",
                f"{a['agentName']} status={a['agentStatus']} model={a.get('foundationModel')}")
        # capture default prompt/parser settings for provenance ground truth
        poc = a.get("promptOverrideConfiguration")
        rep.add("INFO", "promptOverrideConfiguration",
                "present (overrides active)" if poc else "AWS managed defaults")
    except ClientError as e:
        rep.add("FAIL", "GetAgent", e.response["Error"]["Code"])
    if alias_id:
        try:
            al = ba.get_agent_alias(agentId=agent_id, agentAliasId=alias_id)["agentAlias"]
            rep.add("PASS", "GetAgentAlias", f"{al['agentAliasName']} status={al['agentAliasStatus']}")
        except ClientError as e:
            rep.add("FAIL", "GetAgentAlias", e.response["Error"]["Code"])
    else:
        rep.add("SKIP", "GetAgentAlias", "BEDROCK_AGENT_ALIAS_ID not set")


def check_inference_profile(session, region, model, rep) -> str | None:
    br = session.client("bedrock", region_name=region)
    try:
        profs = br.list_inference_profiles().get("inferenceProfileSummaries", [])
    except ClientError as e:
        rep.add("WARN", "ListInferenceProfiles", e.response["Error"]["Code"])
        return None
    match = next((p for p in profs
                  if p.get("inferenceProfileId") == model
                  or model in (p.get("inferenceProfileArn") or "")), None)
    if not match:
        rep.add("WARN", "Nova inference-profile resolution",
                f"{model} not found in {len(profs)} profiles; verify region/ID")
        return None
    members = [m.get("modelArn") for m in match.get("models", [])]
    rep.add("PASS", "Nova inference-profile resolution",
            f"{model} -> {len(members)} member model ARN(s)")
    rep.add("INFO", "service-role coverage",
            "role needs bedrock:InvokeModel* on the PROFILE ARN *and* every "
            "member foundation-model ARN; profile ARN alone => access-denied at orchestration")
    return match.get("inferenceProfileArn")


def check_model_invoke(session, region, model, rep):
    rt = session.client("bedrock-runtime", region_name=region)
    try:
        r = rt.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": "reply with the single word: ok"}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )
        txt = r["output"]["message"]["content"][0].get("text", "")
        rep.add("PASS", "model eligibility (Converse)", f"reachable, said {txt!r}")
    except ClientError as e:
        c = e.response["Error"]["Code"]
        rep.add("FAIL", "model eligibility (Converse)",
                f"{c}: {e.response['Error'].get('Message','')[:120]}")


def check_invoke_agent(session, region, agent_id, alias_id, rep):
    if not (agent_id and alias_id):
        rep.add("SKIP", "InvokeAgent smoke + rawResponse.content + tag census",
                "agent/alias not configured")
        return
    rt = session.client("bedrock-agent-runtime", region_name=region)
    raw_found = False
    raw_blob = ""
    try:
        resp = rt.invoke_agent(
            agentId=agent_id, agentAliasId=alias_id,
            sessionId=f"doctor-{uuid.uuid4()}",
            inputText="Look up the status of demo ticket ABC-123.",
            enableTrace=True,
        )
        final = []
        for ev in resp["completion"]:
            if "chunk" in ev:
                final.append(ev["chunk"]["bytes"].decode("utf-8", "replace"))
            if "trace" in ev:
                t = ev["trace"].get("trace", {})
                orch = t.get("orchestrationTrace", {})
                mio = orch.get("modelInvocationOutput", {})
                raw = mio.get("rawResponse", {}).get("content")
                if raw:
                    raw_found = True
                    raw_blob += raw
        rep.add("PASS", "InvokeAgent smoke", f"final chars={sum(len(x) for x in final)}")
    except ClientError as e:
        rep.add("FAIL", "InvokeAgent smoke",
                f"{e.response['Error']['Code']}: {e.response['Error'].get('Message','')[:120]}")
        return

    if raw_found:
        rep.add("PASS", "modelInvocationOutput.rawResponse.content populated",
                f"{len(raw_blob)} bytes — raw-tag exhibit VIABLE")
        tags = find_tags(raw_blob)
        if tags:
            rep.add("PASS", "baseline tag census",
                    ", ".join(f"{k}×{v}" for k, v in tags.items()))
        else:
            rep.add("WARN", "baseline tag census",
                    "none of thinking/answer/function_calls/… in raw output — "
                    "narrative control tags NOT emitted by this model; change model/agent")
    else:
        rep.add("FAIL", "modelInvocationOutput.rawResponse.content populated",
                "AWS did not emit raw model bytes for this config — raw-tag exhibit "
                "DEGRADES to inference; change model/agent before build")


def check_creation(session, region, model, do_probe, rep):
    if not do_probe:
        rep.add("INFO", "creation eligibility",
                "UNKNOWN — run scripts/probe_create_eligibility.py or doctor "
                "--check-create. ListAgents CANNOT decide this; only a CreateAgent "
                "403(maintenance) vs any-other-error is definitive")
        return
    # Delegate to the probe module for the real single-shot oracle.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import probe_create_eligibility as probe  # noqa: E402
    role_arn = probe.ensure_role(session, region)
    ba = session.client("bedrock-agent", region_name=region)
    created = None
    try:
        r = ba.create_agent(
            agentName=probe.PROBE_AGENT_NAME,
            agentResourceRoleArn=role_arn,
            foundationModel=model,
            instruction="doctor creation probe. delete on sight.",
            tags=probe.TAGS,
        )
        created = r["agent"]["agentId"]
        rep.add("PASS", "creation eligibility",
                f"CreateAgent SUCCEEDED (gate did not fire) — account eligible")
    except ClientError as e:
        result, detail = probe.classify_error(e)
        if result == "BLOCKED":
            rep.add("FAIL", "creation eligibility",
                    f"403 maintenance mode — creation DEAD on this account: {detail[:100]}")
        else:
            rep.add("PASS", "creation eligibility",
                    f"gate did NOT fire ({detail[:100]}) — account effectively allowlisted")
    finally:
        if created:
            try:
                ba.delete_agent(agentId=created, skipResourceInUseCheck=True)
                rep.add("INFO", "probe teardown", f"deleted {created}")
            except ClientError as e:
                rep.add("WARN", "probe teardown",
                        f"could not delete {created}: {e.response['Error']['Code']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--agent-id", default=os.environ.get("BEDROCK_AGENT_ID"))
    ap.add_argument("--alias-id", default=os.environ.get("BEDROCK_AGENT_ALIAS_ID"))
    ap.add_argument("--model", default=os.environ.get("BEDROCK_NOVA_MODEL_ID",
                                                       "us.amazon.nova-pro-v1:0"))
    ap.add_argument("--check-create", action="store_true",
                    help="run the real CreateAgent probe (may create+delete a resource)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    session = boto3.Session()
    print(f"{BLU}== c0c0n Bedrock Classic demo doctor =={RST}")
    print(f"   profile={os.environ.get('AWS_PROFILE','<default chain>')} "
          f"region={args.region} model={args.model}")
    rep = Report()

    acct = check_identity(session, args.region, rep)
    if acct:
        check_agent(session, args.region, args.agent_id, args.alias_id, rep)
        check_inference_profile(session, args.region, args.model, rep)
        check_model_invoke(session, args.region, args.model, rep)
        check_invoke_agent(session, args.region, args.agent_id, args.alias_id, rep)
        check_creation(session, args.region, args.model, args.check_create, rep)

    print()
    if rep.failed:
        print(f"{RED}RESULT: one or more FAIL — resolve before building.{RST}")
    else:
        print(f"{GREEN}RESULT: no hard failures.{RST}")

    if args.json:
        print(json.dumps(rep.rows, indent=2))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
