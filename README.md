# Closing the Tag, 0wning the Agent — c0c0n 2026 demo

Research instrument for the CFP *"Tag Boundary Injection in Bedrock Agent
Orchestration."* Single-user, locally hosted. **Not** a chat product.

> Every view and export identifies its **provider** and **evidence level**. A
> synthetic trace or a local parser result is never presented as proof of a
> Bedrock managed-parser outcome.

**Talk slides:** [Closing the Tag, 0wning the Agent — TagHammer (c0c0n 2026)](https://docs.google.com/presentation/d/1TmLMIKeQ1DhpYqlsS0ShJ76b2Cw1Zu61n3exdsLM15I/edit?usp=sharing)

## Quickstart: deploy → host → destroy

> **Local use only — no authentication.** The scripts and the web app have **no
> login, no authz, no network isolation**. They act with whatever `AWS_PROFILE` /
> credential chain is in your shell and create, mutate and **delete real Bedrock
> agents + an IAM role** on that account. Run on a loopback, single-user machine
> with a scoped, non-production profile. Never expose the backend to a LAN/public
> interface, and never point it at a shared or production account.

> **Scope of the deploy/destroy scripts.** `deploy_agents.py` / `destroy_agents.py`
> exist to stand up (and tear down) the **specific c0c0n talk agents** from
> `scripts/agents_manifest.json` — they are a convenience for reproducing the talk,
> not a requirement. The app itself just reads `data/agents.json` and invokes
> whatever is listed there, so you can **skip the scripts entirely and point the
> app at your own Bedrock agents**: edit `data/agents.json` with your own
> `agent_id` / `alias_id` / `model` and go straight to step 2.

**Prereqs:** Python 3.12, Node 18+, an AWS profile with Bedrock Agents access in a
Bedrock-enabled region (default `us-east-1`), and (optional) Docker. Confirm
eligibility first — see [Running gate (a)](#running-gate-a).

```bash
cp .env.example .env            # set AWS_PROFILE, AWS_REGION, APP_PORT (8080 often = Burp)
export AWS_PROFILE=myiamadmin AWS_REGION=us-east-1
```

### 1. Deploy the agents (AWS Bedrock)

Creates all six Classic agents + the shared `c0c0n-demo-agent-role`, plants a
stable canary in each prompt, and writes the registry `data/agents.json` that the
app reads. Idempotent — safe to re-run.

```bash
python3 -m venv scripts/.venv && . scripts/.venv/bin/activate
pip install -r scripts/requirements.txt
python3 scripts/deploy_agents.py            # [--region-default us-east-1]
deactivate
cat data/agents.json                        # verify the 6 agents + resolved ids/aliases
```

### 2. Host the app (it reads the deployed agents)

The app invokes the **real deployed agents** by the ids in `data/agents.json`;
pick which one in the Chat tab dropdown. Two ways to host:

```bash
# A. Docker (one command; UI on http://127.0.0.1:${APP_PORT:-8080})
make up

# B. Local processes (backend :8000 + Vite preview on APP_PORT)
APP_PORT=8090 ./predemo.sh                  # also re-runs deploy + doctor, prints READY url
```

Open the printed URL → **Setup → Run doctor** (confirm green) → **Chat / Trace**,
pick an agent, Send. Tabs: Chat/Trace, Batch/Tags, Tampering, Setup. See
`RUNBOOK.md` for the presenter walk-through.

### 3. Destroy the setup (app + agents)

Prefix-scoped: deletes only the `c0c0n-demo-*` agents **we created** (plus
aliases) — never an adopted presenter agent.

```bash
./predemo-down.sh                                                 # stop app only, leave agents
./predemo-down.sh --destroy-agents                               # stop app + delete demo agents
./predemo-down.sh --destroy-agents --revert-adopted --delete-role  # full clean (also removes IAM role)

# Or drive the agent teardown directly:
. scripts/.venv/bin/activate
python3 scripts/destroy_agents.py [--revert-adopted] [--delete-role]
```

Full lifecycle details and per-script flags are in the sections below.

## Build order (spec-mandated) — all steps built

The full pipeline (a)→(h) is implemented and verified; see **Status** below.

**(a) FEASIBILITY GATE — green before any demo code was written.** Resolve
creation eligibility, get a working Classic agent, capture a real trace, confirm
both make-or-break signals. Nothing downstream is worth building without a live
trace to ground it.

Then (all done): (b) normalization/fixtures/tests → (c) chat & trace UI →
(d) return-control fixture tool + correlation asserts → (e) batch/tag explorer +
exports → (f) tampering button + bounded engine → (g) Nova Lab fallback →
(h) presenter mode + optional KB.

## Running gate (a)

```bash
cd scripts
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1. Creation-eligibility oracle (single CreateAgent shot, auto-teardown).
#    Creates a scoped service role c0c0n-demo-agent-role if none given.
AWS_PROFILE=myiamadmin python3 probe_create_eligibility.py --region us-east-1

# 2. Full capability report. Add --check-create to fold the probe in.
AWS_PROFILE=myiamadmin python3 doctor.py --region us-east-1 \
    [--agent-id ID --alias-id ID] [--model us.amazon.nova-pro-v1:0]
```

### Reading the probe verdict

| Signal | Meaning | Action |
| --- | --- | --- |
| `403 AccessDeniedException` referencing **maintenance mode / prior service usage** | Creation is **dead** on this account | Use an **existing** invokable Classic agent, or reassign to an allowlisted account. Fallbacks do **not** rescue the CFP claim. |
| Any **other** error (invalid role, validation, throttling) or success | Maintenance gate **did not fire** → account effectively allowlisted | Fix the mundane error, proceed to bootstrap. |

`ListAgents` can **never** decide eligibility — only the `CreateAgent` /
`InvokeInlineAgent` call is the oracle.

### The two make-or-break trace checks (also in `doctor`)

1. `modelInvocationOutput.rawResponse.content` is populated and non-empty — the
   raw-tag scanner has nothing to scan otherwise.
2. Baseline tag census: the chosen model actually emits the narrative's control
   tags (`<thinking>`/`<answer>`/`<function_calls>`…). Nova's function-calling
   format differs and may not. **Choose the model as an output of this census,
   not an input.**

If either fails, the core exhibit degrades to inference — change model/agent
before proceeding.

## Provider modes

- **`classic-live`** (required for the CFP claim): existing/bootstrapped Classic
  agent, Nova inference-profile `foundationModel`, `InvokeAgent(enableTrace=True)`,
  AWS managed default prompt/parser initially.
- **`recorded-live`**: replay of a captured real Classic trace, always badged
  RECORDED with source date.
- **`nova-lab`** (fallback, explicitly labeled): Nova via Bedrock Runtime + local
  versioned prompt + local parser. Banner: *"Local parser reproduction using
  Bedrock Nova; does not establish Bedrock Agents Classic behavior."*

## Day before the demo — one script

```bash
cp .env.example .env                        # set AWS_PROFILE, APP_PORT (8080 is often Burp)
AWS_PROFILE=myiamadmin APP_PORT=8090 ./predemo.sh          # local (proven, fast)
AWS_PROFILE=myiamadmin APP_PORT=8090 ./predemo.sh docker   # containerized
```
`predemo.sh` deploys the backend AWS infra (IAM role + BOTH Classic agents,
updates the test-agent prompt with a stable canary, writes `data/agents.json`),
runs `doctor`, launches the app, and prints the ready URL. Teardown:
```bash
./predemo-down.sh                     # stop app, leave agents
./predemo-down.sh --destroy-agents    # + delete demo-owned agents (never the presenter's)
./predemo-down.sh --destroy-agents --revert-adopted --delete-role   # full clean
```

### Agents (registry: `data/agents.json`)

Six agents, each isolating one variable. Pick in the Chat tab dropdown.

| Key | Model | Tools | Purpose |
| --- | --- | --- | --- |
| `nova-shopping` | nova-pro | no | presenter's shopping agent; direct-channel tag tests (model-resisted) |
| `mistral-tools` | mistral-large | yes | working custom-tool agent; indirect tool-observation injection |
| `tagbot` | mistral-large | no | naive tagged prompt (tags as the only separator); tag structure ≠ security boundary |
| `tagtool` | mistral-large | yes | reliable tool-caller; inject tag sequences into the TOOL RESPONSE vs tag-free control |
| `weak-mistral` | mistral-small | no | weaker-alignment model, same naive prompt as TagBot; does a weaker model leak? |
| `codebot` | mistral-large | code interpreter | `<SYSTEM>`-gated debug: forged `<SYSTEM>` directive crosses the trust boundary → **code execution** |

**codebot / code-interpreter exhibit.** DataDesk reserves its code interpreter
for `<SYSTEM>` diagnostic directives (dev treats `<SYSTEM>` as a trust boundary).
An attacker forges a `<SYSTEM>` directive in a normal user turn → the model runs
attacker Python in AWS's sandbox. Proof lives in the **Code** evidence tab
(`codeInterpreterInvocationOutput.executionOutput`), never in chunk/final text —
a marker echoed in narration is inference, only sandbox `executionOutput` is
proof. The injection is **low-rate** on aligned Mistral Large (~1 in 6 live
sends execute; the rest narrate the tool call as text):

- **Reliable on-stage:** `recorded-live` + `fixtures/codeexec_injection_recorded.json`
  (badged RECORDED_CLASSIC).
- **Live, retried:** send with `retry_until_code: true` (classic-live) — the
  backend re-invokes on a fresh session until a real `executionOutput` lands,
  streams only that attempt, and emits a `note` event with the attempt count
  (honest, not hidden).
- `scripts/capture_codeexec.py` retries until a genuine execution, saves the fixture.

### Create / destroy the Bedrock agents (standalone)

> **Local use only — no authentication.** These scripts and the web app have
> **no login, no authz, and no network isolation of their own.** They act with
> whatever `AWS_PROFILE`/credential chain is in your shell and create, mutate and
> delete real Bedrock agents + an IAM role on that account. Run them **only on a
> loopback/single-user machine** with a scoped, non-production profile. Do **not**
> expose the backend to a LAN or bind it to a public interface, and never point
> them at a shared or production account.

Manifest: `scripts/agents_manifest.json` — all demo agents (adopted Nova shopping
+ owned Mistral agents, incl. `codebot`). Resolved ids are written to
`data/agents.json` (the registry the app reads).

**Create / update (idempotent):**
```bash
# All agents from the manifest. Creates c0c0n-demo-* agents + the shared
# c0c0n-demo-agent-role, updates the adopted agent's instruction with a stable
# canary, writes data/agents.json. Reuses existing agents by name — safe to re-run.
AWS_PROFILE=myiamadmin python3 scripts/deploy_agents.py [--region-default us-east-1]

# Single demo agent (Classic, bare Nova id) + RETURN_CONTROL tools:
AWS_PROFILE=myiamadmin python3 scripts/bootstrap_agent.py --region us-east-1 --model amazon.nova-pro-v1:0
```

**Destroy (prefix-scoped — only agents WE created):**
```bash
# Deletes c0c0n-demo-* agents + aliases. NEVER deletes the adopted presenter agent.
AWS_PROFILE=myiamadmin python3 scripts/destroy_agents.py \
    [--revert-adopted]   # also strip the adopted agent's injected canary
    [--delete-role]      # also remove the shared c0c0n-demo-agent-role

# Single-agent teardown:
AWS_PROFILE=myiamadmin python3 scripts/bootstrap_agent.py --region us-east-1 --teardown
```

`predemo.sh` / `predemo-down.sh` wrap these for the full day-before flow.

## Run the app (manual)

```bash
cp .env.example .env
export AWS_PROFILE=myiamadmin
make up                        # docker compose; UI on http://127.0.0.1:${APP_PORT:-8080}
make test                      # backend unit tests (tag scanner + oracle discipline)
```

Tabs: **Chat / Trace** (3-column live evidence + agent dropdown + injected-tool-
observation editor), **Batch / Tags**, **Tampering** (preview + bounded Quick
run), **Setup** (doctor). Pick the **Mistral tools** agent + **local tool
response** channel to drive the indirect injection live. `APP_PORT` sets the UI
port (8080 collides with Burp — use e.g. 8090). See `RUNBOOK.md`.

## Status (verified live on account <AWS_ACCOUNT_ID>, 2026-09-28)

- [x] Gate (a) **GREEN** — account is Classic-eligible (`CreateAgent` succeeded);
      both make-or-break signals pass on the Nova agent (`rawResponse.content`
      populated, `<thinking>`/`<answer>` emitted).
- [x] Gate scripts: `probe_create_eligibility.py`, `doctor.py`, `bootstrap_agent.py`,
      `capture_trace.py`, `inject_batch.py`.
- [x] Backend (FastAPI + 3 providers + SQLite/SSE + tag scanner + oracle + batch +
      tampering engine), tests green (16 passed), live E2E through the app verified.
- [x] Frontend (Vite/React) builds clean; chat/trace, batch, tampering, setup pages.
- [x] Recorded replay verified offline with the RECORDED badge.

**Key live findings** (see memory + RUNBOOK):
- Use the **bare** Nova model id in Classic (the `us.` profile failed at orchestration).
- **Direct** user-turn tag injection is **model-resisted** (honest negative).
- **Nova + custom action group** is broken on this account — the indirect-tool
  vector needs Claude/us-west-2 or a recorded fixture.

Do not claim the CFP behavior is demonstrated if the live trace does not show it.
