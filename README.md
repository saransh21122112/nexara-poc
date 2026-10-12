# Nexara AI - plugin, skill, MCP server and tool factory (POC)

> Contributing? Read [`CLAUDE.md`](CLAUDE.md) and [`docs/`](docs/README.md) first. Changes follow spec-driven development and arrive as a PR to `dev`.

A public site where companies request plugins, skills, MCP servers and tools, and a private `/factory` dashboard where an engineer turns a request into
a specification with an LLM, tests it, and **delivers it as a private GitHub repository** that the customer's Claude Code loads
directly (skills, a read-only subagent, a slash command). Nexara keeps pushing improvements to the same repo and the
customer's Claude Code picks them up automatically.

Stack: vanilla HTML/CSS/JS, FastAPI, SQLite or Postgres, LangChain + LangGraph (the factory's agent loop), httpx (GitHub REST).

## The flow
1. **Request** (`/request`): the company describes the problem and gives their **GitHub username** and whether they already use Claude Code.
2. **Build and test** (`/factory`): generate the spec with an LLM, edit it, run it in the Playground, run the scenario tests.
3. **Deliver** (Agent Builder -> *Deliver to customer repo*): a preview shows the repo name, file list, who would be invited and an onboarding-message draft. Nothing happens until you confirm. Then the factory creates a **private** repo `nexara-<company>`, commits everything in one commit, and (optionally) invites the customer as a collaborator. A second agent for the same company goes into the **same** repo.
4. **Customer**: accepts the GitHub invite, then runs **one command**: `gh repo clone OWNER/REPO ~/.claude/skills/<company>` (Claude Code loads any folder with a `.claude-plugin/plugin.json` placed there, in every session, with no flags, settings or install step). Alternatives: one-command marketplace install with auto-update (`claude plugin install <company> --marketplace OWNER/REPO`), or, for customers without GitHub, a **zip** downloaded from the Customers page and unzipped into the same folder. `scripts/setup.sh` / `doctor.sh` / `update.sh` help people who are new to Claude Code.
5. **Ongoing** (*Customers* page): per company - repo, invite status (detected from GitHub), agents and whether the repo is behind, onboarding checklist, open GitHub issues, hand-edit warnings, timeline and support notes. Edit an agent -> *Sync to repo* (confirm) pushes a commit and a CHANGELOG entry.

Nothing outward-facing is automatic: creating a repo, inviting someone, pushing an update and re-inviting each need an explicit confirm. The onboarding message is a draft you send yourself.

## What a customer repo contains
The repo root **is** one Claude Code plugin named after the company, holding all of that company's agents:
```
.claude-plugin/plugin.json        plugin manifest (no version: versioned by commit SHA, so every push is an update)
.claude-plugin/marketplace.json   marketplace nexara-<company>: one plugin, source "./"; `renames` maps old plugin names so earlier installs migrate
agents/<agent>.md                 read-only subagent (Read, Grep, Glob)
skills/<agent>/SKILL.md           the entry point: /<company>:<agent> <task>  (one skill + one agent per agent, no duplicates)
README.md START-HERE.md SUPPORT.md CHANGELOG.md custom/ .gitignore .github/ISSUE_TEMPLATE/
scripts/ setup.sh doctor.sh update.sh (+ .ps1)    guided setup / diagnostics / one-command update
snippets/project-settings.json    settings block (github source, autoUpdate true) to share the plugin with a whole project
```
Agent names are readable (`TokenSpendAnalyst` becomes `token-spend-analyst`), descriptions stay short (they are always in Claude's context and drive routing), and instructions that mention factory-only test tools (`calculator`, `lookup_business_data`) are rewritten: the agent does careful arithmetic by hand, shows its working, and says so.

**Updating:** clone installs run `git -C ~/.claude/skills/<company> pull` (or `scripts/update.sh`); marketplace installs update automatically once the customer switches on auto-update (Claude Code only turns it on through its `/plugin` toggle or the settings snippet). There is deliberately **no** auto-pull hook in the repo (a hook is code running on the customer's machine). Both need git credentials that can read the private repo. Old per-agent repos are migrated by *Sync to repo* (old files removed unless hand-edited, `custom/` untouched, `renames` recorded). Use one install method per machine: the same plugin name loads only once.

## Setup
### GitHub (needed for delivery)
Set `GITHUB_OWNER` (the account that will own the repos) and `GITHUB_TOKEN` in `.env` / Render's dashboard. Never commit the token.
- **Personal account:** GitHub gives every collaborator on a private personal-account repo **write** access (read-only collaborators only exist on organization repos), and free personal accounts cannot protect branches on private repos. So customers *can* edit their repo. The factory detects this: files edited by hand are reported as conflicts and never silently overwritten; customer additions belong in `custom/`.
- **Token:** a classic PAT with the `repo` scope works (create repos, commit, invite, read issues). It can reach **all** private repos of that account, so keep it only in secrets, and consider a dedicated bot account or an organization (`GITHUB_OWNER` accepts either; user vs org is auto-detected) once real customers exist.
- Check `GET /api/github/status` or the header badge in the factory.

### Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env        # fill in a provider and GitHub settings
uvicorn backend.main:app --port 8000      # http://localhost:8000  (factory password: nexara-demo)
```

### Docker Desktop
```bash
docker compose up --build -d      # http://localhost:8090 (NEXARA_PORT to change); data in the nexara-db volume
```
`claude_subscription` inside Docker needs `CLAUDE_CODE_OAUTH_TOKEN` in `.env` (from `claude setup-token`).

### Render (public, with Postgres)
`render.yaml` is a Blueprint (Docker web service + Postgres). In the service's Environment tab set `FACTORY_PASSWORD` (generated), `GITHUB_TOKEN`, `GITHUB_OWNER`, and a provider key (`ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` for testing with a subscription). Free web services sleep when idle; free Postgres has a limited lifetime. Factory sessions and rate limits are in memory, so a restart logs you out. Rate limits use the real client address (`CF-Connecting-IP` on Render; forwarded headers are otherwise ignored unless `TRUSTED_PROXY_HOPS` is set). API docs are off on Render.

### Database
SQLite by default (`data/nexara.db`); set `DATABASE_URL=postgresql://...` for Postgres (same schema, created on start; the retired `dispatches` and `api_keys` tables are dropped on start). Tests run on both: `TEST_DATABASE_URL=postgresql://user:pass@localhost:5433/db pytest`.

### AI provider (a switch)
`AI_PROVIDER` or the header dropdown: `anthropic` (API key), `openai` (API key), `claude_subscription` (your Claude plan via `claude-agent-sdk`; **local demo only**, Anthropic's terms restrict using consumer-subscription logins to serve others; built-in tools and account connectors are disabled). An unconfigured provider returns a clear 503; there is no silent fallback.

## Tools in the Playground
`calculator` (AST-whitelisted arithmetic), validated server-side. There is no sample dataset: specs and tests use the customer's own examples or inline synthetic inputs. The agent loop is a LangGraph graph. These tools exist for testing agents in the factory; delivered agents are read-only Claude Code plugins.

## API (all except health, login and `POST /api/requests` need the factory token)
`GET /api/health` `POST /api/factory/login` `GET|PUT /api/provider` `POST|GET /api/requests` `POST /api/requests/{id}/generate-agent` `GET|PUT /api/agents/{id}` `POST /api/agents/{id}/run|evaluate` `GET /api/runs` `GET /api/stats`
Delivery: `GET /api/github/status` `GET /api/agents/{id}/delivery-preview` `POST /api/agents/{id}/deliver` `GET /api/customers` `GET /api/customers/{id}` `POST /api/customers/{id}/notes|onboarding|refresh|sync|reinvite|collaborators` (mutations require `confirm: true`).
Verification: `POST /api/verify` (public) `POST /api/requests/{id}/resend-verification|verify-manually|attach-customer` `GET /api/mail/status`.

## Tests (`pytest`, 160+ tests; SQLite and Postgres)
Fake GitHub (`tests/fake_github.py`) for the client, delivery, sync, conflict, invite, migration and failure paths; generator tests including the real `claude plugin validate` and a real check that a plain folder under `skills/` loads with exactly one skill and one agent per agent; sandboxed runs of the generated `setup.sh` / `doctor.sh` / `update.sh`; an in-place DB upgrade test on SQLite and Postgres. A fake only proves internal consistency, so the same flows were also run live:
- **Real GitHub** (private repos, no invitations sent): create-as-private, one-commit delivery with executable bits, sync, no-op sync, hand edits detected as conflicts, overwrite, and a **real v2 -> v3 migration** of an existing repo. That live run found a bug the fake missed (a reworded starter file in `custom/` blocked the sync); fixed and covered by a regression test.
- **Real Claude Code on clean machines** (fresh `HOME`, no settings): (A) `gh repo clone ... ~/.claude/skills/<name>` then the skill runs and finds planted bugs; (B) `claude plugin install <name> --marketplace OWNER/REPO`; (C) the zip unzipped into `~/.claude/skills`; `setup.sh` and `doctor.sh` run for real; an update pushed from the factory arrived via `update.sh` (path A) and via `marketplace update` + `plugin update` (path B, version = commit SHA); the real interactive terminal UI offers the skill in its slash menu.
- **Arithmetic agent**, 3 of 3 trials fully correct on a known dataset (and earlier 4 of 5 with the old text; one run once refused because it had no calculator).
- **Not verified:** Windows `.ps1` scripts (no PowerShell here), the interactive trust prompt (the folder was pre-trusted), a real customer's first run on their own machine, background auto-update timing over days, the `anthropic`/`openai` providers against their real APIs, and delivery on the Render deployment.

## Limits and honest notes
- Demo authentication (one shared password, in-memory tokens) - **not production security**; no billing or multi-tenancy.
- Customers on a personal-account repo have write access (see above). Auto-update means Nexara changes what runs in a customer's Claude: agents are read-only, hooks are opt-in, every change is in `CHANGELOG.md`, and START-HERE explains how to pause updates.
- The agent's "don't open secret files" rule is an instruction, not a lock (once it ran a count-only search on `.env`); the plugin README ships a `permissions.deny` snippet for a hard block.
- Claude Code needs a paid Claude plan (Pro/Max/Team/Enterprise) or an Anthropic Console account; the free claude.ai plan does not include it.
- Deleting a customer repo is manual (the token needs `delete_repo`, which this app never asks for).

## Request-form assistant
`POST /api/requests/assist` lets a customer describe what they want and fills the request form (or edits one box on request). It is public, so it is limited per IP (`ASSIST_PER_HOUR_PER_IP`, default 10) and per day overall (`ASSIST_PER_DAY`, default 200), input is capped at 1,500 characters, and it can only return the form's text boxes (never email, GitHub or OS). It uses the active provider; if none is configured the form still works by hand.

## Live build and public tracker
`POST /api/requests/{id}/build` (factory login) runs the pipeline: request, plan, specification (one model call), the spec's test scenarios, then the list of files that would ship. Every step is recorded in `build_events`, so the **Live build** view in `/factory` shows only things that really happened, and nothing is pushed to GitHub (delivery stays a separate, confirmed action). The job runs inside the web process: a restart marks a running build as interrupted instead of leaving it spinning.
Customers can follow progress at `/track?id=REQ-...`. That page gets step names and states only (never company, specification, run output or error text) and is limited per IP (`TRACK_PER_HOUR_PER_IP`, default 120).

## Changing delivered work (dev branch + pull request)
After the first delivery, nothing is committed to a customer's `main` any more. Open the customer in `/factory`, press **Change** on an agent, describe the change in plain words, review the proposed spec diff and save it, then **Review files** and **Open pull request**. Nexara builds one commit on top of the repo's current `main`, puts it on the branch `dev` and opens (or updates) a pull request `dev` -> `main`; a person merges on GitHub and presses **Refresh from GitHub**, which records the merge. If `dev` holds commits Nexara did not make, the push is refused until you choose to replace them. Hand edits on `main` still block a proposal until you confirm overwriting them, and `custom/` is never touched. The GitHub token needs *Pull requests: read and write* (fine-grained) or the `repo` scope (classic). GitHub cannot force PR-only merges on private repos of a free personal account, so this is a workflow, not a lock.

## Public pages and contact
About, What we do, How we work, Security, FAQ, Contact, Privacy and Terms are server-rendered from `frontend/pages/` by `backend/pages.py`. They say what customers get and never how it is built (a test enforces a deny-list). Set `CONTACT_EMAIL`, `LEGAL_NAME`, `LEGAL_ADDRESS`, `POLICY_EFFECTIVE` and, once a lawyer has reviewed the drafts, `POLICY_REVIEWED=true`; unset values render nothing. Contact messages are stored (read them under **Messages** in `/factory`) and emailed to `CONTACT_EMAIL` only when SMTP is configured. Limits: `CONTACT_PER_HOUR_PER_IP` (5); `CONTACT_PER_DAY` (100) caps operator emails only, `CONTACT_HARD_PER_DAY` (2000) caps stored messages. Optional `CONTACT_SECRET` for multi-worker setups.

Owner details (name, role, email, phone, location, LinkedIn, GitHub) default to the values in `backend/pages.py` and show on Contact, About and the footer (phone only on Contact). Override with `CONTACT_EMAIL`, `CONTACT_PHONE`, `OWNER_NAME`, `OWNER_ROLE`, `OWNER_LOCATION`, `OWNER_LINKEDIN`, `OWNER_GITHUB`; set one to an empty value to hide it. See `docs/specs/008`.
