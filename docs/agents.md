# Agents

The app reads the registry `data/agents.json` and invokes whatever is listed
there. For the talk, six Classic agents are deployed, each isolating one
variable. Pick one in the Chat tab dropdown.

| Key | Model | Tools | Purpose |
| --- | --- | --- | --- |
| `nova-shopping` | nova-pro | no | shopping agent; direct-channel tag tests (model-resisted) |
| `mistral-tools` | mistral-large | yes | working custom-tool agent; indirect tool-observation injection |
| `tagbot` | mistral-large | no | naive tagged prompt (tags as the only separator); tag structure is not a security boundary |
| `tagtool` | mistral-large | yes | reliable tool-caller; inject tag sequences into the TOOL RESPONSE vs a tag-free control |
| `weak-mistral` | mistral-small | no | weaker-alignment model, same naive prompt as TagBot; does a weaker model leak? |
| `codebot` | mistral-large | code interpreter | `<SYSTEM>`-gated debug: a forged `<SYSTEM>` directive crosses the trust boundary into code execution |

## codebot / code-interpreter exhibit

DataDesk reserves its code interpreter for `<SYSTEM>` diagnostic directives (the
developer treats `<SYSTEM>` as a trust boundary). An attacker forges a `<SYSTEM>`
directive in a normal user turn, and the model runs attacker Python in AWS's
sandbox.

Proof lives in the **Code** evidence tab
(`codeInterpreterInvocationOutput.executionOutput`), never in chunk/final text. A
marker echoed in narration is inference; only sandbox `executionOutput` is proof.

The injection is **low-rate** on aligned Mistral Large (~1 in 6 live sends
execute; the rest narrate the tool call as text). Two reliable ways to show it:

- **Recorded replay:** `recorded-live` + `fixtures/codeexec_injection_recorded.json`
  (badged `RECORDED_CLASSIC`).
- **Live, retried:** send with `retry_until_code: true` (classic-live). The
  backend re-invokes on a fresh session until a real `executionOutput` lands,
  streams only that attempt, and emits a `note` event with the attempt count.

`scripts/capture_codeexec.py` retries until a genuine execution and saves the
fixture.

## Create / destroy the Bedrock agents

> See the single warning in the main [README](../README.md#warning): local use
> only, no auth, acts on real AWS resources with your profile.

These scripts reproduce the talk's agents from `scripts/agents_manifest.json`.
They are optional — point `data/agents.json` at your own agents to skip them.

### Create / update (idempotent)

```bash
# All agents from the manifest. Creates c0c0n-demo-* agents + the shared
# c0c0n-demo-agent-role, updates the adopted agent's instruction with a stable
# canary, writes data/agents.json. Reuses existing agents by name.
AWS_PROFILE=myiamadmin python3 scripts/deploy_agents.py [--region-default us-east-1]

# Single demo agent (Classic, bare Nova id) + RETURN_CONTROL tools:
AWS_PROFILE=myiamadmin python3 scripts/bootstrap_agent.py --region us-east-1 --model amazon.nova-pro-v1:0
```

### Destroy (prefix-scoped — only agents the scripts created)

```bash
# Deletes c0c0n-demo-* agents + aliases. Never deletes an adopted presenter agent.
AWS_PROFILE=myiamadmin python3 scripts/destroy_agents.py \
    [--revert-adopted]   # also strip the adopted agent's injected canary
    [--delete-role]      # also remove the shared c0c0n-demo-agent-role

# Single-agent teardown:
AWS_PROFILE=myiamadmin python3 scripts/bootstrap_agent.py --region us-east-1 --teardown
```

`predemo.sh` / `predemo-down.sh` wrap these for the full day-before flow.
