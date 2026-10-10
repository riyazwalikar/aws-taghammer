"""Doctor as a module: returns structured checks for the /api/doctor endpoint
and the setup page. Never stores credentials. The creation probe is gated
behind an explicit flag because it may create a resource.
"""
from __future__ import annotations

import re
import uuid

import boto3
from botocore.exceptions import ClientError

from .config import settings

TAG_NAMES = ["thinking", "answer", "function_calls", "invoke", "parameters", "final_response"]


def _row(status, check, detail=""):
    return {"status": status, "check": check, "detail": detail}


def run_doctor(check_create: bool = False) -> dict:
    rows = []
    session = boto3.Session()
    region = settings.aws_region
    try:
        ident = session.client("sts").get_caller_identity()
        acct = ident["Account"]
        rows.append(_row("PASS", "sts:GetCallerIdentity",
                         f"account …{acct[-4:]} region {region}"))
    except Exception as e:
        rows.append(_row("FAIL", "sts:GetCallerIdentity", str(e)[:200]))
        return {"rows": rows, "failed": True}

    ba = session.client("bedrock-agent", region_name=region)
    if settings.agent_id:
        try:
            a = ba.get_agent(agentId=settings.agent_id)["agent"]
            rows.append(_row("PASS", "GetAgent",
                             f"{a['agentName']} status={a['agentStatus']} model={a.get('foundationModel')}"))
            poc = a.get("promptOverrideConfiguration")
            rows.append(_row("INFO", "promptOverrideConfiguration",
                             "overrides present" if poc else "AWS managed defaults"))
        except ClientError as e:
            rows.append(_row("FAIL", "GetAgent", e.response["Error"]["Code"]))
        if settings.alias_id:
            try:
                al = ba.get_agent_alias(agentId=settings.agent_id,
                                        agentAliasId=settings.alias_id)["agentAlias"]
                rows.append(_row("PASS", "GetAgentAlias",
                                 f"{al['agentAliasName']} status={al['agentAliasStatus']}"))
            except ClientError as e:
                rows.append(_row("FAIL", "GetAgentAlias", e.response["Error"]["Code"]))
    else:
        rows.append(_row("SKIP", "GetAgent/Alias", "BEDROCK_AGENT_ID not set"))

    # Inference-profile resolution + coverage note (Nova)
    try:
        br = session.client("bedrock", region_name=region)
        profs = br.list_inference_profiles().get("inferenceProfileSummaries", [])
        model = settings.nova_model_id
        match = next((p for p in profs if p.get("inferenceProfileId") == model
                      or model in (p.get("inferenceProfileArn") or "")), None)
        if model.startswith(("us.", "global.")):
            if match:
                rows.append(_row("PASS", "Nova inference-profile resolution",
                                 f"{model} -> {len(match.get('models', []))} member ARN(s)"))
            else:
                rows.append(_row("WARN", "Nova inference-profile resolution",
                                 f"{model} not found; verify region/ID"))
            rows.append(_row("INFO", "service-role coverage",
                             "role needs InvokeModel* on the profile ARN AND every member "
                             "foundation-model ARN; profile ARN alone => access-denied at orchestration"))
        else:
            rows.append(_row("INFO", "model id", f"{model} (bare id; not an inference profile)"))
    except ClientError as e:
        rows.append(_row("WARN", "ListInferenceProfiles", e.response["Error"]["Code"]))

    # Cheap model eligibility via Converse
    try:
        rt = session.client("bedrock-runtime", region_name=region)
        r = rt.converse(modelId=settings.nova_model_id,
                        messages=[{"role": "user", "content": [{"text": "say ok"}]}],
                        inferenceConfig={"maxTokens": 8, "temperature": 0})
        rows.append(_row("PASS", "model eligibility (Converse)",
                         f"reachable: {r['output']['message']['content'][0].get('text','')[:20]!r}"))
    except ClientError as e:
        rows.append(_row("FAIL", "model eligibility (Converse)",
                         f"{e.response['Error']['Code']}: {e.response['Error'].get('Message','')[:100]}"))

    # InvokeAgent smoke + rawResponse.content + tag census
    if settings.agent_id and settings.alias_id:
        try:
            rt2 = session.client("bedrock-agent-runtime", region_name=region)
            resp = rt2.invoke_agent(agentId=settings.agent_id, agentAliasId=settings.alias_id,
                                    sessionId=f"doctor-{uuid.uuid4()}",
                                    inputText="Hello, what can you help me with?",
                                    enableTrace=True)
            raw = ""
            for ev in resp["completion"]:
                if "trace" in ev:
                    mio = ev["trace"].get("trace", {}).get("orchestrationTrace", {}).get("modelInvocationOutput", {})
                    if mio.get("rawResponse", {}).get("content"):
                        raw += mio["rawResponse"]["content"]
            rows.append(_row("PASS", "InvokeAgent smoke", "completed"))
            if raw:
                rows.append(_row("PASS", "rawResponse.content populated",
                                 f"{len(raw)} bytes — raw-tag exhibit viable"))
                found = {t: len(re.findall(rf"<\s*/?\s*{t}\b", raw, re.I)) for t in TAG_NAMES}
                found = {k: v for k, v in found.items() if v}
                rows.append(_row("PASS" if found else "WARN", "baseline tag census",
                                 ", ".join(f"{k}×{v}" for k, v in found.items()) or "no control tags"))
            else:
                rows.append(_row("FAIL", "rawResponse.content populated",
                                 "empty — raw-tag exhibit degrades to inference"))
        except ClientError as e:
            rows.append(_row("FAIL", "InvokeAgent smoke",
                             f"{e.response['Error']['Code']}: {e.response['Error'].get('Message','')[:100]}"))
    else:
        rows.append(_row("SKIP", "InvokeAgent smoke + rawResponse + census", "agent/alias not set"))

    if check_create:
        rows.append(_row("INFO", "creation eligibility",
                         "run scripts/probe_create_eligibility.py for the definitive CreateAgent oracle"))
    else:
        rows.append(_row("INFO", "creation eligibility",
                         "UNKNOWN here; ListAgents cannot decide it. Use the probe script."))

    failed = any(r["status"] == "FAIL" for r in rows)
    return {"rows": rows, "failed": failed, "region": region,
            "agent_id": settings.agent_id, "model": settings.nova_model_id}
