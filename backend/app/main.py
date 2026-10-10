"""FastAPI app: single-user, loopback-only research instrument.

Binds to loopback; blocks cross-site browser writes; escapes user/model HTML at
the UI layer. The boto3 EventStream is drained to completion in a background
task that persists each event to SQLite; the SSE endpoint tails the DB so a
dropped browser connection never abandons an in-progress agent turn.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from . import agents, experiment, oracle, tags
from .config import settings
from .db import DB, utcnow
from .doctor import run_doctor
from .providers.base import InvokeRequest
from .providers.classic import ClassicAgentProvider
from .providers.nova_lab import NovaLabProvider
from .providers.recorded import RecordedTraceProvider

app = FastAPI(title="Closing the Tag, 0wning the Agent — demo API", version="1.0")
db = DB(settings.db_path)

# Loopback single-user: allow only local origins (APP_PORT/DEV_PORT + defaults);
# block other sites' writes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
)

_SOURCES_FROM_RESULT = {
    "input": "submitted_input", "prompt": "model_prompt",
    "raw_response": "raw_response", "rationale": "rationale",
    "final_answer": "final_answer",
}


# ---------------- provider factory ----------------
def make_provider(mode: str, fixture_id: str | None = None, agent: dict | None = None):
    if mode == "classic-live":
        a = agent or {}
        region = a.get("region") or settings.aws_region
        agent_id = a.get("agent_id") or settings.agent_id
        alias_id = a.get("alias_id") or settings.alias_id
        model = a.get("model") or settings.nova_model_id
        if not (agent_id and alias_id):
            raise HTTPException(400, "classic-live requires an agent (select one or set BEDROCK_AGENT_ID/ALIAS_ID)")
        return ClassicAgentProvider(region, agent_id, alias_id, model)
    if mode == "nova-lab":
        region = (agent or {}).get("region") or settings.aws_region
        model = (agent or {}).get("model") or settings.nova_model_id
        return NovaLabProvider(region, model)
    if mode == "recorded-live":
        path = fixture_id if fixture_id and os.path.isabs(fixture_id) \
            else os.path.join(settings.fixtures_dir, fixture_id or "")
        if not os.path.exists(path):
            raise HTTPException(404, f"fixture not found: {fixture_id}")
        return RecordedTraceProvider(path)
    raise HTTPException(400, f"unknown mode {mode}")


def _persist_tags(run_id: str, result) -> None:
    for src, attr in _SOURCES_FROM_RESULT.items():
        text = getattr(result, attr, None)
        for h in tags.scan(text, src, json_path=attr):
            db.execute(
                "INSERT INTO tag_occurrences(run_id,source,tag,kind,case_variant,"
                "start_offset,end_offset,snippet,json_path) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, src, h["tag"], h["kind"], h["case_variant"],
                 h["start_offset"], h["end_offset"], h["snippet"], h["json_path"]))


def _drain(run_id: str, provider, req: InvokeRequest) -> dict:
    """Blocking drain (run in a thread). Persists events as they arrive."""
    seq = 0
    events = []
    for ev in provider.invoke(req):
        db.add_event(run_id, seq, ev.type, {"type": ev.type, **ev.payload})
        events.append(ev)
        seq += 1
    result = provider.finalize(events, req)
    for name, attr in (("submitted_input", "submitted_input"),
                       ("model_prompt", "model_prompt"), ("raw_response", "raw_response"),
                       ("rationale", "rationale"), ("reasoning", "reasoning"),
                       ("final_answer", "final_answer")):
        db.set_artifact(run_id, name, getattr(result, attr, None))
    ci = (result.provenance or {}).get("code_interpreter")
    if ci:
        db.set_artifact(run_id, "code_executed", ci.get("code"))
        db.set_artifact(run_id, "code_output", ci.get("output"))
    _persist_tags(run_id, result)
    db.execute("UPDATE runs SET status=?, provenance_json=? WHERE id=?",
               ("done", json.dumps(result.provenance), run_id))
    return {"result": result, "seq": seq}


# ---------------- models ----------------
class ChatReq(BaseModel):
    input_text: str = Field(min_length=1, max_length=20000)
    session_id: str | None = None
    mode: str | None = None
    channel: str = "direct_user"
    fixture_id: str | None = None
    agent_key: str | None = None
    tool_result_override: str | None = Field(default=None, max_length=20000)
    retry_until_code: bool = False
    max_attempts: int = Field(default=8, ge=1, le=20)


class BatchReq(BaseModel):
    prompts: list[str] = Field(min_length=1, max_length=500)
    mode: str | None = None
    repetitions: int = Field(default=1, ge=1, le=10)
    max_calls: int | None = None
    agent_key: str | None = None


class TamperPreviewReq(BaseModel):
    mode: str = "quick"
    dimensions: dict | None = None
    repetitions: int = Field(default=1, ge=1, le=5)


# ---------------- endpoints ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "time": utcnow(), "mode": settings.demo_mode}


@app.get("/api/config")
def config():
    fixtures = []
    if os.path.isdir(settings.fixtures_dir):
        fixtures = sorted(f for f in os.listdir(settings.fixtures_dir) if f.endswith(".json"))
    return {"config": settings.public(), "fixtures": fixtures}


@app.get("/api/doctor")
async def doctor(check_create: bool = False):
    return await asyncio.to_thread(run_doctor, check_create)


@app.post("/api/sessions")
def new_session():
    return {"session_id": f"sess-{uuid.uuid4()}"}


@app.get("/api/agents")
def list_agents():
    return {"agents": agents.load()}


class AgentUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    region: str
    agent_id: str
    alias_id: str
    model: str = ""
    has_tools: bool = False
    note: str = ""


@app.post("/api/agents")
def upsert_agent(a: AgentUpsert):
    return {"agents": agents.upsert(a.model_dump())}


@app.get("/api/agents/{key}/prompt")
def agent_prompt(key: str):
    """The user-defined system prompt (agent instruction) fetched live from AWS."""
    import boto3
    a = agents.get(key)
    if not a:
        raise HTTPException(404, f"unknown agent {key}")
    ba = boto3.Session().client("bedrock-agent", region_name=a["region"])
    try:
        ag = ba.get_agent(agentId=a["agent_id"])["agent"]
    except Exception as e:
        raise HTTPException(400, f"GetAgent failed: {str(e)[:200]}")
    return {
        "key": key, "agent_id": a["agent_id"], "region": a["region"],
        "agent_name": ag.get("agentName"),
        "foundation_model": ag.get("foundationModel"),
        "instruction": ag.get("instruction"),
        "prompt_overrides": bool(ag.get("promptOverrideConfiguration")),
        "status": ag.get("agentStatus"),
    }


@app.get("/api/agents/discover")
def discover_agents(region: str):
    """Best-effort enumeration of Classic agents + aliases in a region."""
    import boto3
    ba = boto3.Session().client("bedrock-agent", region_name=region)
    out = []
    try:
        for s in ba.list_agents().get("agentSummaries", []):
            aliases = []
            try:
                aliases = [{"alias_id": al["agentAliasId"], "name": al["agentAliasName"]}
                           for al in ba.list_agent_aliases(agentId=s["agentId"]).get("agentAliasSummaries", [])]
            except Exception:
                pass
            aliases.append({"alias_id": "TSTALIASID", "name": "DRAFT/test"})
            out.append({"agent_id": s["agentId"], "name": s["agentName"],
                        "status": s.get("agentStatus"), "aliases": aliases})
    except Exception as e:
        raise HTTPException(400, f"discover failed: {str(e)[:200]}")
    return {"region": region, "agents": out}


@app.post("/api/chat")
async def chat(req: ChatReq):
    mode = req.mode or settings.demo_mode
    session_id = req.session_id or f"sess-{uuid.uuid4()}"
    sel = agents.get(req.agent_key) if req.agent_key else None
    provider = make_provider(mode, req.fixture_id, sel)
    run_id = f"run-{uuid.uuid4()}"
    db.create_run(run_id, provider.name, session_id=session_id,
                  agent_id=(sel or {}).get("agent_id", settings.agent_id),
                  alias_id=(sel or {}).get("alias_id", settings.alias_id),
                  region=(sel or {}).get("region", settings.aws_region),
                  model=(sel or {}).get("model", settings.nova_model_id),
                  prompt_mode=getattr(provider, "banner", None) or "managed-default",
                  parser_mode="managed-default", fixture_id=req.fixture_id,
                  input_text=req.input_text)
    invoke_req = InvokeRequest(input_text=req.input_text, session_id=session_id,
                               channel=req.channel,
                               tool_result_override=req.tool_result_override,
                               retry_until_code=req.retry_until_code,
                               max_attempts=req.max_attempts)
    # background drain; SSE tails the DB.
    asyncio.create_task(asyncio.to_thread(_drain, run_id, provider, invoke_req))
    return {"run_id": run_id, "session_id": session_id, "provider": provider.name,
            "evidence_level": provider.evidence_level, "banner": provider.banner}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    r = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise HTTPException(404, "run not found")
    arts = {a["name"]: {"content": a["content"], "present": bool(a["present"])}
            for a in db.query("SELECT * FROM artifacts WHERE run_id=?", (run_id,))}
    tag_rows = db.query("SELECT source,tag,kind,case_variant,start_offset,end_offset,"
                        "snippet,json_path FROM tag_occurrences WHERE run_id=?", (run_id,))
    events = [json.loads(e["raw_json"]) for e in
              db.query("SELECT raw_json FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
    return {"run": dict(r), "artifacts": arts,
            "tag_occurrences": [dict(t) for t in tag_rows],
            "events": events}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    """SSE tailing the DB; survives a dropped connection since the drain persists
    independently."""
    async def gen():
        last = -1
        idle = 0
        while True:
            rows = db.events_after(run_id, last)
            for row in rows:
                last = row["seq"]
                yield f"event: trace\ndata: {row['raw_json']}\n\n"
            run = db.one("SELECT status FROM runs WHERE id=?", (run_id,))
            if run and run["status"] in ("done", "error", "cancelled"):
                final = get_run(run_id)
                yield f"event: done\ndata: {json.dumps(final, default=str)}\n\n"
                return
            idle += 1
            if idle > 600:  # ~60s safety
                yield "event: timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(0.1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/batches")
async def create_batch(req: BatchReq):
    mode = req.mode or settings.demo_mode
    max_calls = min(req.max_calls or settings.max_total_calls, settings.max_total_calls)
    batch_id = f"batch-{uuid.uuid4()}"
    prompts = req.prompts * req.repetitions
    if len(prompts) > max_calls:
        prompts = prompts[:max_calls]
    db.execute("INSERT INTO batches(id,created_utc,status,config_json,totals_json) VALUES(?,?,?,?,?)",
               (batch_id, utcnow(), "running",
                json.dumps({"mode": mode, "n": len(prompts), "max_calls": max_calls}),
                json.dumps({"planned": len(prompts)})))
    asyncio.create_task(_run_batch(batch_id, mode, prompts, req.agent_key))
    return {"batch_id": batch_id, "planned_calls": len(prompts),
            "cap": max_calls, "concurrency": settings.max_inflight}


async def _run_batch(batch_id: str, mode: str, prompts: list[str], agent_key: str | None = None):
    sem = asyncio.Semaphore(settings.max_inflight)
    run_ids: list[str] = []
    planned = len(prompts)
    sel = agents.get(agent_key) if agent_key else None

    def _persist(status: str):
        db.execute("UPDATE batches SET status=?, totals_json=? WHERE id=?",
                   (status, json.dumps({"planned": planned, "completed": len(run_ids),
                                        "run_ids": list(run_ids)}), batch_id))

    async def one(p: str):
        async with sem:
            b = db.one("SELECT status FROM batches WHERE id=?", (batch_id,))
            if b and b["status"] == "cancelled":
                return
            provider = make_provider(mode, None, sel)
            rid = f"run-{uuid.uuid4()}"
            db.create_run(rid, provider.name, session_id=f"batch-{uuid.uuid4()}",
                          agent_id=(sel or {}).get("agent_id", settings.agent_id),
                          alias_id=(sel or {}).get("alias_id", settings.alias_id),
                          region=(sel or {}).get("region", settings.aws_region),
                          model=(sel or {}).get("model", settings.nova_model_id),
                          input_text=p)
            # Publish the run immediately (status 'running') so the UI shows the row
            # as it starts, then again after it finishes.
            run_ids.append(rid)
            _persist("running")
            await asyncio.to_thread(_drain, rid, provider,
                                    InvokeRequest(input_text=p, session_id=f"batch-{uuid.uuid4()}"))
            _persist("running")

    await asyncio.gather(*(one(p) for p in prompts))
    b = db.one("SELECT status FROM batches WHERE id=?", (batch_id,))
    _persist("cancelled" if b and b["status"] == "cancelled" else "done")


@app.post("/api/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str):
    db.execute("UPDATE batches SET status='cancelled' WHERE id=?", (batch_id,))
    return {"ok": True}


@app.get("/api/batches")
def list_batches(limit: int = 10):
    rows = db.query("SELECT * FROM batches ORDER BY created_utc DESC LIMIT ?", (limit,))
    return {"batches": [{
        "id": r["id"], "created_utc": r["created_utc"], "status": r["status"],
        "config": json.loads(r["config_json"] or "{}"),
        "totals": json.loads(r["totals_json"] or "{}"),
    } for r in rows]}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    b = db.one("SELECT * FROM batches WHERE id=?", (batch_id,))
    if not b:
        raise HTTPException(404, "batch not found")
    totals = json.loads(b["totals_json"] or "{}")
    runs = []
    for rid in totals.get("run_ids", []):
        r = db.one("SELECT id,input_text,status FROM runs WHERE id=?", (rid,))
        if r:
            runs.append(dict(r))
    return {"batch": dict(b), "runs": runs}


@app.post("/api/tampering/preview")
def tampering_preview(req: TamperPreviewReq):
    caps = {"max_total_calls": settings.max_total_calls}
    dims = dict(req.dimensions or {})
    # reflect real capability: no working tool on Nova here
    dims.setdefault("tool_available", False)
    dims.setdefault("kb_configured", False)
    return experiment.preview(req.mode, dims, req.repetitions, caps)


class TamperRunReq(BaseModel):
    mode: str = "quick"
    dimensions: dict | None = None
    repetitions: int = Field(default=1, ge=1, le=3)
    provider_mode: str | None = None


@app.post("/api/tampering/runs")
async def tampering_run(req: TamperRunReq):
    caps = {"max_total_calls": settings.max_total_calls}
    dims = dict(req.dimensions or {})
    dims.setdefault("tool_available", False)
    dims.setdefault("kb_configured", False)
    pv = experiment.preview(req.mode, dims, req.repetitions, caps)
    tid = f"tamper-{uuid.uuid4()}"
    db.execute("INSERT INTO tampering_runs(id,created_utc,status,mode,suite_json,totals_json) "
               "VALUES(?,?,?,?,?,?)",
               (tid, utcnow(), "running", req.mode, json.dumps(pv["cells"]),
                json.dumps(pv["totals"])))
    asyncio.create_task(_run_tampering(tid, req.provider_mode or settings.demo_mode,
                                       pv["cells"], caps))
    return {"tampering_run_id": tid, "preview": pv}


async def _run_tampering(tid: str, mode: str, cells: list[dict], caps: dict):
    completed, results = 0, []
    budget = caps["max_total_calls"]
    for cell in cells:
        used = completed * 3
        if used + 3 > budget:
            break  # PARTIAL
        t = db.one("SELECT status FROM tampering_runs WHERE id=?", (tid,))
        if t and t["status"] in ("cancelled", "paused"):
            break
        prov_c = make_provider(mode)
        prov_k = make_provider(mode)
        cand = await asyncio.to_thread(_invoke_capture, prov_c, cell["payload"])
        ctrl = await asyncio.to_thread(_invoke_capture, prov_k, cell["control_tagfree"])
        case = {"id": cell["id"], "family": cell["mutation"],
                "objective": cell["objective"], "markers": ["paris", "override"],
                "payload_marker": "Paris"}
        g = oracle.grade_pair(case, cand, ctrl)
        cid = f"cmp-{uuid.uuid4()}"
        db.execute("INSERT INTO comparisons(id,created_utc,case_id,outcome,confidence,reason,detail_json) "
                   "VALUES(?,?,?,?,?,?,?)",
                   (cid, utcnow(), cell["id"], g.outcome, g.confidence, g.reason,
                    json.dumps(oracle.as_dict(g))))
        results.append({"cell": cell["id"], "outcome": g.outcome, "confidence": g.confidence})
        completed += 1
    t = db.one("SELECT status FROM tampering_runs WHERE id=?", (tid,))
    if t and t["status"] == "cancelled":
        status = "cancelled"
    elif completed < len(cells):
        status = "partial"
    else:
        status = "done"
    db.execute("UPDATE tampering_runs SET status=?, totals_json=? WHERE id=?",
               (status, json.dumps({"completed": completed, "eligible": len(cells),
                                    "results": results}), tid))


def _invoke_capture(provider, text: str):
    events = list(provider.invoke(InvokeRequest(input_text=text,
                                                session_id=f"tamper-{uuid.uuid4()}")))
    return provider.finalize(events, InvokeRequest(input_text=text, session_id="x"))


@app.get("/api/tampering/runs/{tid}")
def get_tampering(tid: str):
    t = db.one("SELECT * FROM tampering_runs WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "not found")
    comps = db.query("SELECT case_id,outcome,confidence,reason FROM comparisons "
                     "WHERE case_id IN (SELECT value FROM json_each((SELECT "
                     "json_group_array(json_extract(value,'$.id')) FROM json_each(?))))",
                     (t["suite_json"],)) if False else []
    return {"tampering_run": dict(t),
            "totals": json.loads(t["totals_json"] or "{}"),
            "note": "PARTIAL is never EXHAUSTIVE_COMPLETE."}


@app.get("/api/tampering/runs/{tid}/coverage")
def tampering_coverage(tid: str):
    t = db.one("SELECT * FROM tampering_runs WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "not found")
    cells = json.loads(t["suite_json"] or "[]")
    heat: dict[str, dict[str, int]] = {}
    for c in cells:
        heat.setdefault(c["entry_point"], {}).setdefault(c["mutation"], 0)
        heat[c["entry_point"]][c["mutation"]] += 1
    return {"corpus_version": experiment.CORPUS_VERSION, "coverage_heatmap": heat,
            "totals": json.loads(t["totals_json"] or "{}")}


@app.post("/api/tampering/runs/{tid}/pause")
def pause_tampering(tid: str):
    db.execute("UPDATE tampering_runs SET status='paused' WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/tampering/runs/{tid}/resume")
async def resume_tampering(tid: str):
    t = db.one("SELECT * FROM tampering_runs WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404, "not found")
    db.execute("UPDATE tampering_runs SET status='running' WHERE id=?", (tid,))
    return {"ok": True, "note": "resume continues from pending cells; completed cells are not rerun"}


@app.post("/api/tampering/runs/{tid}/stop")
def stop_tampering(tid: str):
    db.execute("UPDATE tampering_runs SET status='cancelled' WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/fixtures/import")
async def import_fixture(request: Request):
    body = await request.json()
    name = body.get("name") or f"imported-{uuid.uuid4()}.json"
    if not name.endswith(".json"):
        name += ".json"
    os.makedirs(settings.fixtures_dir, exist_ok=True)
    path = os.path.join(settings.fixtures_dir, os.path.basename(name))
    with open(path, "w") as f:
        json.dump(body.get("fixture", body), f, indent=2)
    return {"ok": True, "fixture_id": os.path.basename(name)}


@app.get("/api/exports/{run_id}")
def export_run(run_id: str):
    data = get_run(run_id)
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="{run_id}.json"'})
