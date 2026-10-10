"""Agent registry: the selectable Classic agents (id/alias/region/model/label).

Seeded from a JSON file (persisted next to the DB) merged with the env default
and a few known live agents. The UI lists these; each chat/batch call may pick
one, and a provider is built for that agent's region/model per request.
"""
from __future__ import annotations

import json
import os

from .config import settings

# Known live agents on this account (verified 2026-09-28/29).
_SEED = [
    {"key": "nova-shopping", "label": "Nova shopping (no tools)",
     "region": "us-east-1", "agent_id": "SDECXM5QWK", "alias_id": "TSTALIASID",
     "model": "amazon.nova-pro-v1:0", "has_tools": False,
     "note": "presenter's agent; direct-channel tag tests (model-resisted)"},
    {"key": "mistral-tools", "label": "Mistral ticketing (RETURN_CONTROL)",
     "region": "us-east-1", "agent_id": "GW9LLYQGA9", "alias_id": "B6RTGHPVRT",
     "model": "mistral.mistral-large-2402-v1:0", "has_tools": True,
     "note": "working custom-tool agent; indirect tool-observation injection"},
]


def _registry_path() -> str:
    d = os.path.dirname(settings.db_path) or "."
    return os.path.join(d, "agents.json")


def _env_default() -> dict | None:
    if not (settings.agent_id and settings.alias_id):
        return None
    return {"key": "env-default", "label": f"env: {settings.agent_id}",
            "region": settings.aws_region, "agent_id": settings.agent_id,
            "alias_id": settings.alias_id, "model": settings.nova_model_id,
            "has_tools": False, "note": "from .env"}


def load() -> list[dict]:
    path = _registry_path()
    items: list[dict] = []
    if os.path.exists(path):
        try:
            items = json.load(open(path))
        except (ValueError, OSError):
            items = []
    if not items:
        items = list(_SEED)
    # ensure env default present (by agent_id)
    env = _env_default()
    if env and not any(a.get("agent_id") == env["agent_id"] for a in items):
        items = [env] + items
    return items


def save(items: list[dict]) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    json.dump(items, open(path, "w"), indent=2)


def get(key: str) -> dict | None:
    for a in load():
        if a.get("key") == key:
            return a
    return None


def upsert(item: dict) -> list[dict]:
    items = load()
    items = [a for a in items if a.get("key") != item["key"]]
    items.append(item)
    save(items)
    return items
