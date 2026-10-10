#!/usr/bin/env python3
"""
Gate (a): Bedrock Agents Classic creation-eligibility probe.

Spec rule: `CreateAgent` is its own oracle. Attempt it ONCE with a valid service
role so the call reaches a clean signal, then read the error:

  * 403 AccessDeniedException referencing "Maintenance Mode" / "prior service
    usage"  -> creation is DEAD on this account. Live `classic-live` path via
    creation is impossible; must reassign to an already-eligible account or use
    an existing invokable Classic agent.
  * ANY other error (invalid role, validation, throttling) OR success -> the
    maintenance gate did NOT fire; the account is effectively allowlisted.
    Fix the mundane error and proceed.

Never infer eligibility from ListAgents. This script does the real CreateAgent
call and, if an agent is actually created, deletes it afterward.

Usage:
    AWS_PROFILE=myiamadmin python3 scripts/probe_create_eligibility.py \
        --region us-east-1 [--role-arn arn:aws:iam::...:role/...] [--keep]

If --role-arn is omitted, the script creates a scoped demo role
(c0c0n-demo-agent-role) with a bedrock.amazonaws.com trust policy, uses it, and
leaves it in place (it is reused by the real bootstrap). Pass --delete-role to
remove it at the end.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

ROLE_NAME = "c0c0n-demo-agent-role"
PROBE_AGENT_NAME = "c0c0n-eligibility-probe"
NAME_PREFIX = "c0c0n-demo"
TAGS = {"project": "c0c0n-2026-tag-boundary-demo", "managed-by": "probe"}

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def ensure_role(session, region) -> str:
    iam = session.client("iam")
    try:
        r = iam.get_role(RoleName=ROLE_NAME)
        eprint(f"[role] reusing existing {ROLE_NAME}")
        return r["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    eprint(f"[role] creating {ROLE_NAME}")
    r = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
        Description="c0c0n 2026 Bedrock Classic demo agent service role",
        Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
    )
    arn = r["Role"]["Arn"]
    # Minimal inline policy so the role can invoke models at orchestration time.
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="bedrock-invoke-model",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                        "Resource": "*",
                    }
                ],
            }
        ),
    )
    eprint("[role] waiting for IAM propagation (10s)")
    time.sleep(10)
    return arn


def classify_error(err: ClientError) -> tuple[str, str]:
    code = err.response.get("Error", {}).get("Code", "")
    msg = err.response.get("Error", {}).get("Message", "")
    status = err.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    low = msg.lower()
    maintenance = status == 403 and (
        "maintenance mode" in low or "prior service usage" in low
    )
    if maintenance:
        return "BLOCKED", msg
    return "GATE_DID_NOT_FIRE", f"{code}: {msg} (HTTP {status})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--role-arn", default=None)
    ap.add_argument("--model", default="us.amazon.nova-pro-v1:0",
                    help="inference-profile ID for the agent foundationModel")
    ap.add_argument("--keep", action="store_true",
                    help="do not delete a probe agent if one is created")
    ap.add_argument("--delete-role", action="store_true")
    args = ap.parse_args()

    session = boto3.Session()
    ident = session.client("sts").get_caller_identity()
    acct = ident["Account"]
    eprint(f"[sts] account={acct} arn={ident['Arn']} region={args.region}")

    role_arn = args.role_arn or ensure_role(session, args.region)
    eprint(f"[role] using {role_arn}")

    agent = session.client("bedrock-agent", region_name=args.region)

    verdict = {"account": acct, "region": args.region, "model": args.model,
               "role_arn": role_arn}
    created_id = None
    try:
        eprint(f"[probe] CreateAgent name={PROBE_AGENT_NAME} model={args.model}")
        resp = agent.create_agent(
            agentName=PROBE_AGENT_NAME,
            agentResourceRoleArn=role_arn,
            foundationModel=args.model,
            instruction="Probe agent for c0c0n creation-eligibility gate. Delete on sight.",
            tags=TAGS,
        )
        created_id = resp["agent"]["agentId"]
        verdict["result"] = "GATE_DID_NOT_FIRE"
        verdict["detail"] = f"CreateAgent SUCCEEDED, agentId={created_id}"
        eprint(f"[probe] SUCCESS agentId={created_id} -> account is eligible")
    except ClientError as e:
        result, detail = classify_error(e)
        verdict["result"] = result
        verdict["detail"] = detail
        eprint(f"[probe] ClientError -> {result}: {detail}")

    # Teardown any real agent we created.
    if created_id and not args.keep:
        eprint(f"[teardown] DeleteAgent {created_id}")
        for _ in range(30):
            try:
                agent.delete_agent(agentId=created_id, skipResourceInUseCheck=True)
                verdict["teardown"] = f"deleted {created_id}"
                break
            except ClientError as e:
                c = e.response["Error"]["Code"]
                if c in ("ConflictException", "ValidationException"):
                    time.sleep(3)
                    continue
                verdict["teardown"] = f"FAILED to delete {created_id}: {c}"
                eprint(f"[teardown] {verdict['teardown']}")
                break
        else:
            verdict["teardown"] = f"TIMED OUT deleting {created_id} - remove manually"

    if args.delete_role and not args.role_arn:
        iam = session.client("iam")
        try:
            iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName="bedrock-invoke-model")
            iam.delete_role(RoleName=ROLE_NAME)
            verdict["role_cleanup"] = f"deleted {ROLE_NAME}"
        except ClientError as e:
            verdict["role_cleanup"] = f"FAILED: {e.response['Error']['Code']}"

    print(json.dumps(verdict, indent=2))
    if verdict["result"] == "BLOCKED":
        eprint("\n=== VERDICT: CREATION BLOCKED (maintenance mode 403). ===")
        eprint("classic-live via creation is dead on this account. Options:")
        eprint("  - use an EXISTING invokable Classic agent/alias in an eligible account")
        eprint("  - reassign the live path to an allowlisted account")
        return 2
    eprint("\n=== VERDICT: GATE DID NOT FIRE -> account effectively allowlisted. ===")
    eprint("Fix any mundane error above and proceed with bootstrap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
