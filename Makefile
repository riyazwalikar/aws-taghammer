SHELL := /bin/bash
PROFILE ?= myiamadmin
REGION  ?= us-east-1

.PHONY: help up down logs probe doctor bootstrap teardown test backend-dev frontend-dev capture

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n",$$1,$$2}'

up: ## docker compose up --build (UI on http://127.0.0.1:8080)
	docker compose up --build

down: ## stop and remove containers
	docker compose down

logs: ## tail backend logs
	docker compose logs -f backend

probe: ## gate (a): CreateAgent creation-eligibility oracle (may create+delete an agent)
	cd scripts && python3 -m venv .venv && . .venv/bin/activate && pip install -q -r requirements.txt && \
	AWS_PROFILE=$(PROFILE) python3 probe_create_eligibility.py --region $(REGION)

doctor: ## capability report against the configured agent (read-only)
	cd scripts && . .venv/bin/activate && AWS_PROFILE=$(PROFILE) python3 doctor.py --region $(REGION) \
	  $${BEDROCK_AGENT_ID:+--agent-id $$BEDROCK_AGENT_ID} $${BEDROCK_AGENT_ALIAS_ID:+--alias-id $$BEDROCK_AGENT_ALIAS_ID}

bootstrap: ## create the demo Classic agent (Nova bare id) + RETURN_CONTROL tools
	cd scripts && . .venv/bin/activate && AWS_PROFILE=$(PROFILE) python3 bootstrap_agent.py --region $(REGION) --model amazon.nova-pro-v1:0

teardown: ## delete the demo agent (prefix-scoped)
	cd scripts && . .venv/bin/activate && AWS_PROFILE=$(PROFILE) python3 bootstrap_agent.py --region $(REGION) --teardown

capture: ## capture a live baseline trace to fixtures/ (needs AGENT/ALIAS env)
	cd scripts && . .venv/bin/activate && AWS_PROFILE=$(PROFILE) python3 capture_trace.py --region $(REGION) \
	  --agent-id $$BEDROCK_AGENT_ID --alias-id $$BEDROCK_AGENT_ALIAS_ID

test: ## run backend unit tests (tag scanner + oracle discipline)
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -q pytest && python3 -m pytest tests -q

backend-dev: ## run backend locally on :8000
	cd backend && . .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

frontend-dev: ## run frontend dev server on :5173 (proxies /api to :8000)
	cd frontend && npm install && npm run dev
