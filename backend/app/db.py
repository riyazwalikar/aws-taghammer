"""SQLite persistence. Append-only trace store; never overwrite a prior trace.

Tables: runs, invocations, events, artifacts, tag_occurrences, comparisons,
batches, tampering_runs. WAL mode. A schema_version row gates migrations.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL,
  provider TEXT NOT NULL,
  session_id TEXT,
  agent_id TEXT, alias_id TEXT, region TEXT, model TEXT,
  prompt_mode TEXT, parser_mode TEXT,
  fixture_id TEXT,
  input_text TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  provenance_json TEXT
);

CREATE TABLE IF NOT EXISTS invocations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  kind TEXT,                 -- initial | resume
  invocation_id TEXT,        -- returnControl invocationId correlation
  created_utc TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,      -- stream order, preserved
  etype TEXT NOT NULL,       -- trace | chunk | returnControl | failure | other
  raw_json TEXT NOT NULL,
  created_utc TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  name TEXT NOT NULL,        -- submitted_input | model_prompt | raw_response | rationale | final_answer | reasoning
  content TEXT,
  present INTEGER NOT NULL DEFAULT 1,   -- 0 => "not provided by AWS"
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS tag_occurrences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,      -- input | prompt | raw_response | rationale | observation | final_answer
  tag TEXT NOT NULL,
  kind TEXT NOT NULL,        -- open | close | self | unmatched
  case_variant TEXT,
  start_offset INTEGER, end_offset INTEGER,
  snippet TEXT, json_path TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS comparisons (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL,
  case_id TEXT,
  candidate_run TEXT, control_run TEXT, baseline_run TEXT,
  outcome TEXT, confidence TEXT, reason TEXT,
  detail_json TEXT
);

CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL,
  status TEXT NOT NULL,      -- running | done | cancelled | partial | error
  config_json TEXT,
  totals_json TEXT
);

CREATE TABLE IF NOT EXISTS tampering_runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL,
  status TEXT NOT NULL,      -- preview | running | paused | done | partial | cancelled
  mode TEXT,                 -- quick | coverage | exhaustive
  suite_json TEXT,
  totals_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_tags_run ON tag_occurrences(run_id, source);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
"""


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class DB:
    """Thread-safe-ish SQLite wrapper (one connection guarded by a lock)."""

    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(_DDL)
            cur = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
            row = cur.fetchone()
            if row is None:
                self._conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)",
                                   (str(SCHEMA_VERSION),))
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()):
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def executemany(self, sql: str, seq_params):
        with self._lock:
            cur = self._conn.executemany(sql, seq_params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- domain helpers ---
    def create_run(self, run_id: str, provider: str, **fields) -> None:
        cols = ["id", "created_utc", "provider", "status"]
        vals = [run_id, utcnow(), provider, fields.pop("status", "running")]
        for k in ("session_id", "agent_id", "alias_id", "region", "model",
                  "prompt_mode", "parser_mode", "fixture_id", "input_text"):
            cols.append(k); vals.append(fields.get(k))
        cols.append("provenance_json"); vals.append(json.dumps(fields.get("provenance", {})))
        ph = ",".join("?" * len(cols))
        self.execute(f"INSERT INTO runs({','.join(cols)}) VALUES({ph})", vals)

    def add_event(self, run_id: str, seq: int, etype: str, raw: dict) -> None:
        self.execute(
            "INSERT INTO events(run_id,seq,etype,raw_json,created_utc) VALUES(?,?,?,?,?)",
            (run_id, seq, etype, json.dumps(raw, default=str), utcnow()))

    def set_artifact(self, run_id: str, name: str, content: str | None) -> None:
        self.execute(
            "INSERT INTO artifacts(run_id,name,content,present) VALUES(?,?,?,?)",
            (run_id, name, content, 1 if content else 0))

    def set_run_status(self, run_id: str, status: str) -> None:
        self.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))

    def events_after(self, run_id: str, after_seq: int) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq",
            (run_id, after_seq))
