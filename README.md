# Nexara AI - Agent Factory (POC)

A working proof of concept: a public site where companies request AI agents, and a private `/factory` dashboard where an engineer turns a request into an agent specification with an LLM, edits it, runs it with controlled tools, tests it, and exports a handover bundle.

Stack: vanilla HTML/CSS/JS, FastAPI, SQLite or Postgres, **LangChain + LangGraph** (agent loop and model access), python-dotenv.

## Run on a Mac
```bash
cd nexara-poc
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then choose a provider below
uvicorn backend.main:app --port 8000
```
Open http://localhost:8000 (site), http://localhost:8000/request (form), http://localhost:8000/factory (dashboard, demo password `nexara-demo`).
The SQLite DB is created at `data/nexara.db` on first start and survives restarts.

## Run with Docker Desktop
```bash
docker compose up --build -d      # one container serves the API and the frontend
open http://localhost:8090        # change the host port with NEXARA_PORT=xxxx
docker compose logs -f            # logs;  docker compose down  to stop (data is kept)
```
- Config comes from `.env` (optional file). The SQLite DB lives in the `nexara-db` volume, so it survives rebuilds; `docker compose down -v` deletes it.
- **Providers in Docker:** `anthropic` / `openai` work with their API keys in `.env`. `claude_subscription` works only if you put a `CLAUDE_CODE_OAUTH_TOKEN` in `.env` (run `claude setup-token` on your Mac). Your normal Mac login lives in the Keychain and cannot be shared with a container; without the token the dashboard shows "not configured" with this explanation. Subscription use is still local-demo only.
- Host port 8090 is the default because 8000 is often taken by other projects.

## Database: SQLite locally, Postgres in production
By default the app uses SQLite (`data/nexara.db`). Set `DATABASE_URL=postgresql://...` and it uses Postgres instead (same schema, created on start). The whole test suite passes on both: `TEST_DATABASE_URL=postgresql://user:pass@localhost:5433/db pytest`.

## Deploy on Render (public, with Postgres)
`render.yaml` is a Blueprint: a Docker web service plus a Postgres database. Render's disk is ephemeral, which is why production uses Postgres.
1. Put this folder in a GitHub repo (`.env` and `data/*.db` are git-ignored; never commit secrets).
2. Render dashboard -> **New -> Blueprint** -> select the repo -> apply.
3. In the new service's *Environment*: read the generated `FACTORY_PASSWORD`, and set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` + `AI_PROVIDER=openai`). Without a key, customer calls return a clear 503.
4. Open `https://<service>.onrender.com/factory`. The public URL is picked up automatically (`RENDER_EXTERNAL_URL`) and shown in the customer-API panel.
Rate limits use the real client address: forwarded headers are ignored unless you set `TRUSTED_PROXY_HOPS` (the blueprint sets it to 1 for Render's single proxy). After the first deploy, confirm limits are per-client rather than shared by all callers (if every user shares one bucket, the proxy chain has more hops than assumed).
Testing with a Claude subscription on Render: add `CLAUDE_CODE_OAUTH_TOKEN` in the service's Environment tab (value from `claude setup-token`; it also sits in your local `.env`) and pick *Claude subscription* in the factory header. This is for the factory only: the customer API (`/v1/run`) stays disabled on that provider. The Anthropic API provider needs a real API key (`sk-ant-api…` from console.anthropic.com), which is a different credential from the OAuth token. Notes: free web services sleep when idle (slow first call; use a paid plan for always-on customer traffic) and free Postgres has a limited lifetime - check Render's current terms. Factory sessions and rate limits are in memory, so a restart logs you out. Do **not** use the Claude-subscription provider on a public host.

## Choosing the AI provider (a switch)
Set `AI_PROVIDER` in `.env`, or switch live from the dropdown in the dashboard header (runtime choice is not persisted across restarts).

| Provider | Needs | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` (+ `ANTHROPIC_MODEL`) | LangChain `ChatAnthropic`. Use this for client-facing work. |
| `openai` | `OPENAI_API_KEY` (+ `OPENAI_MODEL`) | LangChain `ChatOpenAI`. |
| `claude_subscription` | `claude` CLI installed and `claude login` done | Uses your Claude plan via `claude-agent-sdk`. **Local single-user demo only**: Anthropic's terms restrict using consumer-subscription logins to power services for others. Built-in tools and your account's connectors are disabled; only the two registered tools exist. Token counts may be reported differently, and spec output is JSON-by-prompt rather than schema-forced. |

Keys live only in the server's `.env` (git-ignored) and are never sent to the browser. If the selected provider is not configured, the API returns HTTP 503 with an explicit message; nothing is faked and there is no silent fallback to another provider.

## The demo journey
1. `/` -> pick an agent card (prefills the form) -> submit at `/request` -> real request ID.
2. `/factory` -> Client Requests -> select it -> **Generate Agent** (real LLM call, validated against the `AgentSpec` schema, saved).
3. Edit the spec -> **Save Changes**.
4. Agent Playground -> run a task; the execution log shows agent, task, provider/model, status, latency, tokens (when reported), tool calls and errors.
5. **Run Test** runs up to 3 spec scenarios with deterministic checks and shows the pass rate.
6. Agent Builder -> **Export** a `portable` (spec + runner + Dockerfile) or `crewai` (YAML config + main.py) bundle for handover.

## Dispatching an agent to a customer
Agent Builder -> **Dispatch to customer** creates a dispatch (a snapshot of the spec at that moment) with:
- a **package** (`.zip`): the agent runtime, an authenticated **HTTP API** (`POST /run`, needs a customer-chosen `AGENT_API_KEY`), an **MCP server** (Claude Desktop / Cursor / other agents), `tools.py`, Dockerfile + compose, and a README with connection steps. The customer supplies their own Anthropic/OpenAI key.
- a **draft email** to the request's contact address. **Nothing is sent by the system**: you review it, send it yourself (copy, or "Open in my mail app"), then click "I sent it - mark as sent". No keys or secrets are included in the email or package.
Verified: the package builds, runs in its own Docker container (auth, `/spec`, honest 503 without an LLM key, MCP handshake) and its tool loop works against the real Anthropic/OpenAI SDKs on fake servers. **Not verified:** a live LLM call from inside a delivered package (needs a customer API key). The sample `lookup_business_data` reads bundled sample data; the customer must connect their own systems (README step 4).

### Customer API keys (hosted calls)
In the dispatch panel, **Customer API access -> Create API key** issues a per-customer key (`nxk_...`). The customer calls Nexara's server:
```bash
curl -s https://YOUR-HOST/v1/run -H "Authorization: Bearer nxk_..." -H "Content-Type: application/json" -d '{"task": "..."}'
```
`GET /v1/agent` returns the agent's name, purpose, capabilities and schemas (not its instructions). Keys are shown once and stored only as a SHA-256 hash; they can be revoked instantly, show a call count and last-used time, and are limited to 30 requests/minute each (`CUSTOMER_API_RATE_PER_MIN`; in-memory per process). A key runs the **dispatched snapshot** of the agent, can't reach the factory API, and failures return a reference ID instead of internal errors. Every call is logged as an `api` run.
- **Disabled on the `claude_subscription` provider** (503): subscription logins are for local demos, not for serving customers. Use `anthropic` or `openai`.
- **Needs a reachable host.** On localhost a customer can't call it: deploy it somewhere public and set `PUBLIC_BASE_URL` so the UI shows the right URL. The in-app auth, rate limit and key store are POC-grade (no TLS termination, no per-customer quotas or billing).
- Verified with the real server process, browser UI and shell calls against a **fake LLM server**; not against live Anthropic/OpenAI (no API key available).

## Tools
`calculator` (AST-whitelisted arithmetic, no `eval`) and `lookup_business_data` (sales/customers/orders from `data/sample_business_data.json`). Arguments are validated server-side; unknown or non-enabled tools are refused. The agent loop is a LangGraph `StateGraph` (agent -> tools -> agent).

## API
`GET /api/health`, `POST/GET /api/requests`, `GET /api/requests/{id}`, `POST /api/requests/{id}/generate-agent`, `GET /api/agents`, `GET/PUT /api/agents/{id}`, `POST /api/agents/{id}/run`, `POST /api/agents/{id}/evaluate`, plus `GET /api/agents/{id}/export?format=`, `POST /api/agents/{id}/dispatch`, `GET /api/dispatches/{id}/package`, `POST /api/dispatches/{id}/mark-sent`, `POST|GET /api/dispatches/{id}/keys`, `POST /api/keys/{id}/revoke`, customer API `GET /v1/agent` + `POST /v1/run`, `GET /api/runs`, `GET /api/stats`, `GET/PUT /api/provider`, `POST /api/factory/login`. Docs at `/docs`.

## Tests
`pytest` - 32 tests. LLM calls are mocked or pointed at local fake servers in tests only; the app itself never mocks. `tests/test_providers.py` runs the real LangChain Anthropic/OpenAI clients against fake local servers (plumbing only, not proof of live-API behaviour).

## What is and is not implemented
Implemented and exercised: request form + persistence, dashboard views, spec generation / validation / editing, playground with tools and logs, deterministic scenario tests, provider switch, exports (files verified to be generated; **the exported CrewAI/portable projects have not been executed**).

Live-verified (API, browser desktop + 375px mobile, empty-DB zero states, restart persistence) with the `claude_subscription` provider on `claude-sonnet-5-5` only. The `anthropic` and `openai` paths are covered by unit tests and the not-configured error path but have **not been run against the real APIs** (no keys were available while building).

Not implemented: real authentication (the dashboard uses a single shared demo password and in-memory tokens - **not production security**; the public form is unauthenticated and unthrottled), billing, multi-tenancy, multi-agent orchestration, streaming, deployment, rate limiting, LLM-judged evaluation. "Run Test" is a basic prototype check (non-empty reply, keywords, expected tool call), not a comprehensive model evaluation.

## Roadmap / handover to clients
Exports are starter scaffolds; client tools must be wired to their real systems (HTTP/MCP connectors). Candidate next steps: run crews on CrewAI for multi-agent work, deploy per client in containers, and add a client-facing chat UI (e.g. CopilotKit + AG-UI) on top of the same `/run` endpoint.
