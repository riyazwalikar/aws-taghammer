# AWS TagHammer — Fuzzer/Harness for C0C0N 2026 talk — Closing the Tag, 0wning the Agent

A local research harness for **tag-boundary injection in Bedrock Agent
orchestration**. It drives real Bedrock Classic agents, captures their traces, and
shows where injected XML-style tags cross a trust boundary. Every view and export
labels its **provider** and **evidence level** — a synthetic trace or a local
parser result is never shown as proof of a Bedrock managed-parser outcome.

**Reference blog:** https://ibreak.software/2026/10/closing-the-tag-0wning-the-agent-c0c0n-2026/

![TagHammer Chat / Trace dashboard](screenshots/taghammer-dashboard-1.png)
*Chat / Trace: submitted input, the real Bedrock model prompt, raw model response
with control tags highlighted, and the trace timeline — plus tag occurrences
attributed by source.*

![codebot code-execution example](screenshots/taghammer-codebot-rce-example.png)
*codebot exhibit: a forged `<SYSTEM>` directive drives the code interpreter; the
sandbox prints the `RCE_PROOF_9Z7Q` marker and `os.uname()` output.*

![Batch fuzzer](screenshots/taghammer-fuzzer-1.png)
*Batch / Tags: run a set of tag-injection variations against the selected agent
and inspect each run's evidence.*

<a name="warning"></a>
> ⚠️ **Local use only, no authentication.** The scripts and the web app have no
> login, no authz, and no network isolation. They act with whatever `AWS_PROFILE`
> / credential chain is in your shell and can create, modify and **delete real
> Bedrock agents and an IAM role** on that account. Run on a loopback, single-user
> machine with a scoped, non-production profile. Never expose the backend to a LAN
> or public interface, and never point it at a shared or production account.

## Quickstart: deploy → host → destroy

**Prereqs:** Python 3.12, Node 18+, an AWS profile with Bedrock Agents access in a
Bedrock-enabled region (default `us-east-1`), and optionally Docker. Confirm
account eligibility first — see [docs/feasibility.md](docs/feasibility.md).

```bash
cp .env.example .env            # set AWS_PROFILE, AWS_REGION, APP_PORT (8080 often = Burp)
export AWS_PROFILE=myiamadmin AWS_REGION=us-east-1
```

The deploy/destroy scripts exist to reproduce the **talk's specific agents** and
are optional. The app invokes whatever is in `data/agents.json`, so to run against
**your own Bedrock agents**, put their `agent_id` / `alias_id` / `model` in that
file and skip to step 2.

### 1. Deploy the talk's agents

Creates the six Classic agents + the shared `c0c0n-demo-agent-role`, plants a
stable canary in each prompt, and writes `data/agents.json`. Idempotent.

```bash
python3 -m venv scripts/.venv && . scripts/.venv/bin/activate
pip install -r scripts/requirements.txt
python3 scripts/deploy_agents.py            # [--region-default us-east-1]
deactivate
cat data/agents.json                        # verify the agents + resolved ids/aliases
```

See [docs/agents.md](docs/agents.md) for the roster and per-script flags.

### 2. Host the app

The app invokes the deployed agents by the ids in `data/agents.json`; choose one
in the Chat tab dropdown.

```bash
# A. Docker (UI on http://127.0.0.1:${APP_PORT:-8080})
make up

# B. Local processes (backend :8000 + Vite preview on APP_PORT)
APP_PORT=8090 ./predemo.sh                  # also re-runs deploy + doctor, prints the ready URL
```

Open the URL → **Setup → Run doctor** (confirm green) → **Chat / Trace**, pick an
agent, Send. See [RUNBOOK.md](RUNBOOK.md) for the presenter walk-through.

### 3. Destroy

Prefix-scoped: deletes only the `c0c0n-demo-*` agents the scripts created (plus
aliases), never an adopted presenter agent.

```bash
./predemo-down.sh                                                  # stop app only, leave agents
./predemo-down.sh --destroy-agents                                # stop app + delete demo agents
./predemo-down.sh --destroy-agents --revert-adopted --delete-role # full clean (also removes the IAM role)
```

## Documentation

- [docs/agents.md](docs/agents.md) — agent roster, the codebot code-execution exhibit, create/destroy flags.
- [docs/feasibility.md](docs/feasibility.md) — eligibility gate, trace checks, provider modes, verified findings.
- [RUNBOOK.md](RUNBOOK.md) — presenter walk-through.
- [spec.md](spec.md) — full design spec.

## Development

```bash
make test        # backend unit tests (tag scanner + oracle discipline)
make backend-dev # uvicorn on :8000 with reload
make frontend-dev # Vite dev server on :5173
```

Tabs: **Chat / Trace**, **Batch / Tags**, **Tampering**, **Setup**. `APP_PORT`
sets the UI port (8080 collides with Burp — use e.g. 8090).

## License

[MIT](LICENSE) © 2026 Riyaz Walikar
