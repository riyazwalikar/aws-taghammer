# Feasibility gate and provider modes

Before any demo is worth running, the account must be able to create and invoke a
Classic agent that emits the control tags the exhibit depends on.

## Running the gate

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
| `403 AccessDeniedException` referencing **maintenance mode / prior service usage** | Creation is dead on this account | Use an existing invokable Classic agent, or reassign to an allowlisted account |
| Any other error (invalid role, validation, throttling) or success | Maintenance gate did not fire → account effectively allowlisted | Fix the mundane error, proceed to bootstrap |

`ListAgents` can never decide eligibility — only the `CreateAgent` /
`InvokeInlineAgent` call is the oracle.

### The two make-or-break trace checks (also in `doctor`)

1. `modelInvocationOutput.rawResponse.content` is populated and non-empty — the
   raw-tag scanner has nothing to scan otherwise.
2. Baseline tag census: the chosen model actually emits the narrative's control
   tags (`<thinking>`/`<answer>`/`<function_calls>`…). Nova's function-calling
   format differs and may not. Choose the model as an output of this census, not
   an input.

If either fails, the core exhibit degrades to inference — change model/agent
before proceeding.

## Provider modes

- **`classic-live`** (required for the core claim): existing/bootstrapped Classic
  agent, Nova inference-profile `foundationModel`, `InvokeAgent(enableTrace=True)`,
  AWS managed default prompt/parser initially.
- **`recorded-live`**: replay of a captured real Classic trace, always badged
  `RECORDED` with its source date.
- **`nova-lab`** (fallback, explicitly labeled): Nova via Bedrock Runtime + a
  local versioned prompt + local parser. Banner: *"Local parser reproduction
  using Bedrock Nova; does not establish Bedrock Agents Classic behavior."*

## Verified findings

Verified live on a Classic-eligible account, 2026-09-28:

- Gate (a) green — `CreateAgent` succeeded; both make-or-break signals pass on the
  Nova agent (`rawResponse.content` populated, `<thinking>`/`<answer>` emitted).
- Backend (FastAPI + 3 providers + SQLite/SSE + tag scanner + oracle + batch +
  tampering engine), 16 tests passing, live end-to-end through the app.
- Frontend (Vite/React) builds clean: chat/trace, batch, tampering, setup.
- Recorded replay verified offline with the `RECORDED` badge.

Key behaviors:

- Use the **bare** Nova model id in Classic (the `us.` inference profile failed at
  orchestration on the test account).
- **Direct** user-turn tag injection is **model-resisted** (an honest negative).
- **Nova + custom action group** was broken on the test account; the indirect-tool
  vector needs Claude/us-west-2 or a recorded fixture.

Rule for the stage: do not claim the behavior is demonstrated if the live trace
does not show it.
