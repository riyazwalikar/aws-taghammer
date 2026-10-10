#!/usr/bin/env python3
"""Destroy demo-OWNED agents from agents_manifest.json (prefix-scoped).

Deletes only entries with a "name" (c0c0n-demo-*) — the agents WE created —
plus their aliases. NEVER deletes an "adopt_agent_id" entry (the presenter's own
agent); it only reverts that agent's instruction if --revert-adopted is passed.
--delete-role also removes the shared c0c0n-demo-agent-role.

Usage:
    AWS_PROFILE=myiamadmin python3 destroy_agents.py [--revert-adopted] [--delete-role]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "agents_manifest.json")


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def delete_owned(entry: dict):
    region = entry["region"]
    ba = boto3.Session().client("bedrock-agent", region_name=region)
    name = entry["name"]
    target = None
    for page in ba.get_paginator("list_agents").paginate():
        for s in page.get("agentSummaries", []):
            if s["agentName"] == name:
                target = s["agentId"]
    if not target:
        eprint(f"[destroy] {name}: not found (already gone)")
        return
    for al in ba.list_agent_aliases(agentId=target).get("agentAliasSummaries", []):
        try:
            ba.delete_agent_alias(agentId=target, agentAliasId=al["agentAliasId"])
            eprint(f"[destroy] {name}: deleted alias {al['agentAliasId']}")
        except ClientError as e:
            eprint(f"[destroy] {name}: alias {al['agentAliasId']} {e.response['Error']['Code']}")
    for _ in range(30):
        try:
            ba.delete_agent(agentId=target, skipResourceInUseCheck=True)
            eprint(f"[destroy] {name}: deleted agent {target}")
            return
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ConflictException", "ValidationException"):
                time.sleep(3); continue
            eprint(f"[destroy] {name}: {e.response['Error']['Code']}")
            return


def revert_adopted(entry: dict):
    region = entry["region"]
    ba = boto3.Session().client("bedrock-agent", region_name=region)
    aid = entry["adopt_agent_id"]
    try:
        a = ba.get_agent(agentId=aid)["agent"]
    except ClientError as e:
        eprint(f"[revert] {aid}: {e.response['Error']['Code']}"); return
    # strip the operational-note canary line -> plain instruction
    base = entry["instruction"].split(" Operational note")[0]
    ba.update_agent(agentId=aid, agentName=a["agentName"],
                    agentResourceRoleArn=a["agentResourceRoleArn"],
                    foundationModel=a.get("foundationModel"),
                    instruction=base,
                    idleSessionTTLInSeconds=a.get("idleSessionTTLInSeconds", 600))
    ba.prepare_agent(agentId=aid)
    eprint(f"[revert] {aid}: instruction reverted (canary removed)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert-adopted", action="store_true")
    ap.add_argument("--delete-role", action="store_true")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST))
    for entry in manifest:
        if "name" in entry:
            delete_owned(entry)
        elif "adopt_agent_id" in entry and args.revert_adopted:
            revert_adopted(entry)
        else:
            eprint(f"[destroy] leaving adopted agent {entry.get('adopt_agent_id')} untouched")

    if args.delete_role:
        iam = boto3.Session().client("iam")
        R = "c0c0n-demo-agent-role"
        try:
            for p in iam.list_role_policies(RoleName=R)["PolicyNames"]:
                iam.delete_role_policy(RoleName=R, PolicyName=p)
            iam.delete_role(RoleName=R)
            eprint(f"[destroy] deleted role {R}")
        except ClientError as e:
            eprint(f"[destroy] role {R}: {e.response['Error']['Code']}")
    eprint("[destroy] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
