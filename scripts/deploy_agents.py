#!/usr/bin/env python3
"""Idempotent deploy of ALL demo agents from agents_manifest.json.

Two kinds of manifest entry:
  * adopt_agent_id  -> an EXISTING agent (e.g. presenter's SDECXM5QWK). We update
    its instruction (with a stable synthetic canary) and re-prepare it. We NEVER
    delete an adopted agent.
  * name            -> a demo-owned agent we create/reuse by name (c0c0n-demo-*),
    with a RETURN_CONTROL action group when tools=true. Destroyable.

Canaries are generated once and persisted in deploy_state.json so every redeploy
keeps the same value (stable demos). The resolved id/alias/model for each agent
is written to <repo>/data/agents.json — the registry the web app reads.

Usage:
    AWS_PROFILE=myiamadmin python3 deploy_agents.py [--region-default us-east-1]
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time

import boto3
from botocore.exceptions import ClientError

import probe_create_eligibility as probe
import bootstrap_agent as boot

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MANIFEST = os.path.join(HERE, "agents_manifest.json")
STATE = os.path.join(HERE, "deploy_state.json")
REGISTRY = os.path.join(REPO, "data", "agents.json")


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except (ValueError, OSError):
            return {}
    return {}


def save_state(s: dict):
    json.dump(s, open(STATE, "w"), indent=2)


def canary_for(state: dict, key: str) -> str:
    canaries = state.setdefault("canaries", {})
    if key not in canaries:
        canaries[key] = f"DEMO_CANARY_{secrets.token_hex(4)}"
    return canaries[key]


def deploy_adopted(entry: dict, canary: str) -> dict:
    """Update an existing agent's instruction + re-prepare. Never deletes."""
    region = entry["region"]
    ba = boto3.Session().client("bedrock-agent", region_name=region)
    aid = entry["adopt_agent_id"]
    a = ba.get_agent(agentId=aid)["agent"]
    instr = entry["instruction"].format(canary=canary)
    eprint(f"[adopt] {aid} ({a['agentName']}) — updating instruction")
    ba.update_agent(agentId=aid, agentName=a["agentName"],
                    agentResourceRoleArn=a["agentResourceRoleArn"],
                    foundationModel=a.get("foundationModel", entry["model"]),
                    instruction=instr,
                    idleSessionTTLInSeconds=a.get("idleSessionTTLInSeconds", 600))
    ba.prepare_agent(agentId=aid)
    boot.wait_agent_status(ba, aid, "PREPARED")
    return {"agent_id": aid, "alias_id": entry.get("alias_id", "TSTALIASID"),
            "model": a.get("foundationModel", entry["model"]),
            "agent_name": a.get("agentName")}


def deploy_owned(entry: dict, canary: str) -> dict:
    """Create/reuse a demo-owned agent by name; add RETURN_CONTROL tools if asked."""
    region = entry["region"]
    session = boto3.Session()
    ba = session.client("bedrock-agent", region_name=region)
    role_arn = probe.ensure_role(session, region)
    name = entry["name"]
    instr = entry["instruction"].format(canary=canary)

    existing = None
    for page in ba.get_paginator("list_agents").paginate():
        for s in page.get("agentSummaries", []):
            if s["agentName"] == name:
                existing = s
                break
    if existing:
        aid = existing["agentId"]
        eprint(f"[owned] reusing {name} ({aid}) — updating model+instruction")
        ba.update_agent(agentId=aid, agentName=name, agentResourceRoleArn=role_arn,
                        foundationModel=entry["model"], instruction=instr,
                        idleSessionTTLInSeconds=600)
    else:
        eprint(f"[owned] creating {name} model={entry['model']}")
        aid = ba.create_agent(agentName=name, agentResourceRoleArn=role_arn,
                              foundationModel=entry["model"], instruction=instr,
                              idleSessionTTLInSeconds=600, tags=probe.TAGS)["agent"]["agentId"]
        boot.wait_agent_status(ba, aid, "NOT_PREPARED")

    ags = ba.list_agent_action_groups(agentId=aid, agentVersion="DRAFT").get("actionGroupSummaries", [])
    if entry.get("tools"):
        if not any(g["actionGroupName"] == boot.ACTION_GROUP for g in ags):
            eprint(f"[owned] creating action group {boot.ACTION_GROUP} (RETURN_CONTROL)")
            ba.create_agent_action_group(
                agentId=aid, agentVersion="DRAFT", actionGroupName=boot.ACTION_GROUP,
                actionGroupExecutor={"customControl": "RETURN_CONTROL"},
                functionSchema={"functions": boot.FUNCTION_SCHEMA}, actionGroupState="ENABLED")
    if entry.get("code_interpreter"):
        if not any(g["actionGroupName"] == "CodeInterpreter" for g in ags):
            eprint("[owned] enabling AMAZON.CodeInterpreter")
            ba.create_agent_action_group(
                agentId=aid, agentVersion="DRAFT", actionGroupName="CodeInterpreter",
                parentActionGroupSignature="AMAZON.CodeInterpreter", actionGroupState="ENABLED")

    ba.prepare_agent(agentId=aid)
    boot.wait_agent_status(ba, aid, "PREPARED")

    alias_name = entry.get("alias_name", "c0c0n-demo-alias")
    aliases = ba.list_agent_aliases(agentId=aid).get("agentAliasSummaries", [])
    alias = next((x for x in aliases if x["agentAliasName"] == alias_name), None)
    if alias:
        alias_id = alias["agentAliasId"]
        # Re-point the alias to a fresh version from the just-prepared DRAFT, else
        # the alias keeps serving the OLD prompt snapshot (create froze version 1).
        eprint(f"[owned] updating alias {alias_name} -> new DRAFT version")
        ba.update_agent_alias(agentId=aid, agentAliasId=alias_id, agentAliasName=alias_name)
    else:
        eprint(f"[owned] creating alias {alias_name}")
        alias_id = ba.create_agent_alias(agentId=aid, agentAliasName=alias_name,
                                          tags=probe.TAGS)["agentAlias"]["agentAliasId"]
    return {"agent_id": aid, "alias_id": alias_id, "model": entry["model"],
            "agent_name": name}


def main() -> int:
    manifest = json.load(open(MANIFEST))
    state = load_state()
    session = boto3.Session()
    acct = session.client("sts").get_caller_identity()["Account"]
    eprint(f"[deploy] account …{acct[-4:]}")

    registry = []
    for entry in manifest:
        canary = canary_for(state, entry["key"])
        if "adopt_agent_id" in entry:
            res = deploy_adopted(entry, canary)
        else:
            res = deploy_owned(entry, canary)
        registry.append({
            "key": entry["key"], "label": entry["label"], "region": entry["region"],
            "agent_id": res["agent_id"], "alias_id": res["alias_id"],
            "agent_name": res.get("agent_name"),
            "model": res["model"], "has_tools": bool(entry.get("tools")),
            "note": entry.get("note", ""),
        })
        eprint(f"  -> {entry['key']}: {res['agent_id']} / {res['alias_id']} "
               f"(canary {canary})")

    save_state(state)
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    json.dump(registry, open(REGISTRY, "w"), indent=2)
    print(json.dumps({"deployed": registry, "registry": REGISTRY}, indent=2))
    eprint(f"\n[deploy] wrote registry {REGISTRY} ({len(registry)} agents)")
    eprint("[deploy] canaries in deploy_state.json (stable across redeploys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
