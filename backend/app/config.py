"""Runtime configuration, sourced from environment (.env). No secrets stored."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float_or_none(name: str):
    v = os.environ.get(name, "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass
class Settings:
    aws_profile: str | None = field(default_factory=lambda: os.environ.get("AWS_PROFILE") or None)
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    agent_id: str | None = field(default_factory=lambda: os.environ.get("BEDROCK_AGENT_ID") or None)
    alias_id: str | None = field(default_factory=lambda: os.environ.get("BEDROCK_AGENT_ALIAS_ID") or None)
    nova_model_id: str = field(default_factory=lambda: os.environ.get("BEDROCK_NOVA_MODEL_ID", "amazon.nova-pro-v1:0"))
    demo_mode: str = field(default_factory=lambda: os.environ.get("DEMO_MODE", "classic-live"))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "/data/demo.sqlite3"))
    fixtures_dir: str = field(default_factory=lambda: os.environ.get("FIXTURES_DIR", "/fixtures"))
    app_port: int = field(default_factory=lambda: _int("APP_PORT", 8080))
    dev_port: int = field(default_factory=lambda: _int("DEV_PORT", 5173))

    def allowed_origins(self) -> list[str]:
        ports = {self.app_port, self.dev_port, 8080, 5173}
        origins = []
        for p in ports:
            origins += [f"http://127.0.0.1:{p}", f"http://localhost:{p}"]
        return origins

    # Local safety caps for the experiment engine.
    max_inflight: int = field(default_factory=lambda: _int("MAX_INFLIGHT", 1))
    max_total_calls: int = field(default_factory=lambda: _int("MAX_TOTAL_CALLS", 30))
    max_output_tokens: int = field(default_factory=lambda: _int("MAX_OUTPUT_TOKENS", 1024))
    max_wall_seconds: int = field(default_factory=lambda: _int("MAX_WALL_SECONDS", 600))
    max_estimated_spend_usd: float | None = field(default_factory=lambda: _float_or_none("MAX_ESTIMATED_SPEND_USD"))
    resource_prefix: str = field(default_factory=lambda: os.environ.get("RESOURCE_PREFIX", "c0c0n-demo"))

    def public(self) -> dict:
        """Config safe to show in the UI (never credentials)."""
        return {
            "aws_profile": self.aws_profile,
            "aws_region": self.aws_region,
            "agent_id": self.agent_id,
            "alias_id": self.alias_id,
            "nova_model_id": self.nova_model_id,
            "demo_mode": self.demo_mode,
            "caps": {
                "max_inflight": self.max_inflight,
                "max_total_calls": self.max_total_calls,
                "max_output_tokens": self.max_output_tokens,
                "max_wall_seconds": self.max_wall_seconds,
                "max_estimated_spend_usd": self.max_estimated_spend_usd,
            },
        }


settings = Settings()
