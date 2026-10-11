# Nexara AI - Agent Factory (POC)

A public site where companies request AI agents, and a private `/factory` dashboard where an engineer turns a request into
an agent with an LLM, tests it, and **delivers it as a private GitHub repository** that the customer's Claude Code loads
directly (skills, a read-only subagent, a slash command). Nexara keeps pushing improvements to the same repo and the
customer's Claude Code picks them up automatically.

Stack: vanilla HTML/CSS/JS, FastAPI, SQLite or Postgres, LangChain + LangGraph (the factory's agent loop), httpx (GitHub REST).

## The flow
1. **Request** (`/request`): the company describes the problem and gives their **GitHub username** and whether they already use Claude Code.
2. **Build and test** (`/factory`): generate the spec with an LLM, edit it, run it in the Playground, run the scenario tests.
3. **Deliver** (Agent Builder -> *Deliver to customer repo*): a preview shows the repo name, file list, who would be invited and an onboarding-message draft. Nothing happens until you confirm. Then the factory creates a **private** repo `nexara-<company>`, commits everything in one commit, and (optionally) invites the customer as a collaborator. A second agent for the same company goes into the **same** repo.
4. **Customer**: accepts the GitHub invite, runs `scripts/setup.sh` (Windows: `setup.ps1`) which installs Claude Code only after asking and connects the agents to their project with auto-update on, or simply clones the repo and opens it in Claude Code.
5. **Ongoing** (*Customers* page): per company - repo, invite status (detected from GitHub), agents and whether the repo is behind, onboarding checklist, open GitHub issues, hand-edit warnings, timeline and support notes. Edit an agent -> *Sync to repo* (confirm) pushes a commit and a CHANGELOG entry.

Nothing outward-facing is automatic: creating a repo, inviting someone, pushing an update and re-inviting each need an explicit confirm. The onboarding message is a draft you send yourself.

## What a customer repo contains
```
README.md START-HERE.md SUPPORT.md CHANGELOG.md CLAUDE.md
.claude-plugin/marketplace.json     marketplace nexara-<company>, one relative-path plugin per agent, NO version (versioned by commit SHA)
.claude/settings.json               clone mode: registers the repo itself + git pull on session start
plugins/<agent>/                    agents/ (read-only: Read, Grep, Glob), skills/, commands/, hooks/hooks.optional.json (inactive)
custom/                             the customer's own area: Nexara never touches it
scripts/ setup.sh doctor.sh (+ .ps1)    guided setup / diagnostics
snippets/project-settings.json      the settings block for the customer's own project (github source + autoUpdate: true)
```
Auto-update, as verified against real Claude Code and real GitHub: with that settings block alone (no manual install) Claude Code fetches the private marketplace, records `autoUpdate`, and loads the plugin. A push from the factory reaches the customer after Claude Code's background refresh (a few minutes after a session starts, applied on next launch or `/reload-plugins`). It needs working git credentials (`gh auth login && gh auth setup-git`) and the customer trusting the folder.

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
`calculator` (AST-whitelisted arithmetic) and `lookup_business_data` (sample data), validated server-side. The agent loop is a LangGraph graph. These tools exist for testing agents in the factory; delivered agents are read-only Claude Code plugins.

## API (all except health, login and `POST /api/requests` need the factory token)
`GET /api/health` `POST /api/factory/login` `GET|PUT /api/provider` `POST|GET /api/requests` `POST /api/requests/{id}/generate-agent` `GET|PUT /api/agents/{id}` `POST /api/agents/{id}/run|evaluate` `GET /api/runs` `GET /api/stats`
Delivery: `GET /api/github/status` `GET /api/agents/{id}/delivery-preview` `POST /api/agents/{id}/deliver` `GET /api/customers` `GET /api/customers/{id}` `POST /api/customers/{id}/notes|onboarding|refresh|sync|reinvite|collaborators` (mutations require `confirm: true`).
Verification: `POST /api/verify` (public) `POST /api/requests/{id}/resend-verification|verify-manually|attach-customer` `GET /api/mail/status`.

## Tests (`pytest`, 100+ tests; SQLite and Postgres)
Fake GitHub (`tests/fake_github.py`) for the client, delivery, sync, conflict, invite and failure paths; generator tests including the real `claude plugin validate`; sandboxed runs of the generated `setup.sh`/`doctor.sh`. A fake only proves internal consistency, so the same flow was also run live:
- **Verified live against real GitHub** (throwaway private repo, no invitations sent): create-as-private, one-commit delivery with executable bits, sync, no-op sync, a hand edit made through GitHub's API detected as a conflict and not overwritten, overwrite, CHANGELOG, issues API.
- **Verified with the real Claude CLI:** the generated marketplace and plugins validate; a customer project with only the generated settings block loaded the private marketplace, the plugin, `/<agent>` and the subagent in a clean config; the agent found planted bugs in a sample repo and did not open `.env`; a pushed update arrived as a new commit-SHA version.
- **Not verified:** Windows `.ps1` scripts (no PowerShell here), a real customer accepting an invitation, background auto-update timing on a customer's machine over days, the `anthropic`/`openai` providers against their real APIs, and delivery on the Render deployment (needs `GITHUB_TOKEN` there).

## Protection against forged requests (the request form is public)
The form is unauthenticated and the email is not verified, so a stranger could claim to be an existing customer. Defences:
- A request joins an existing customer only if **company, GitHub username and email all match**; otherwise it becomes a separate customer with its own repo. Merging is a deliberate staff action (Client Requests -> *Attach to this customer*, with a confirmation and a timeline entry).
- Only the customer's **established** GitHub user is ever invited. A request naming a different user blocks the invitation before anything is created; that person must be added explicitly (*Invite another person*) after you verify them.
- Adding to an **existing** repo shows who submitted the request and needs an explicit acknowledgement (`existing_repo_ack`).
- **Email verification:** submitting the form emails the requester a confirmation link. **Nothing can be delivered for a request until its email is verified** (the preview shows why it is blocked and the confirm button is disabled). The link is single-use, expires in 48 hours, and is opened with a click on a page (so mail scanners that pre-open links cannot confirm for the user). The token sits in the URL fragment, so it never reaches server logs or referrers, and only its hash is stored. The email is a fixed template that contains nothing the requester typed. Limits: 1 email per address per 5 minutes, 3 per address per day, per-IP limits on the form and on confirm attempts. The public response is identical whether or not mail is configured.
- **Staff alternatives:** *Resend confirmation email* (confirm click) or *Mark verified...* (you confirmed them another way; the note is saved in the customer's timeline).
- **No SMTP yet?** Set `REQUIRE_EMAIL_VERIFICATION=false` (default is on). Delivery then works without a confirmation email, every preview shows a warning, the header says "Email verification OFF", and no mail is attempted. The other protections (all-three-attributes matching, established-GitHub-user invites, existing-repo acknowledgement, your confirm click) stay on, so you remain the control: check each request before delivering. Turn it back on once SMTP works.
- Verifying an email proves the mailbox, not the GitHub account: invitations still follow the rules above.
- Setup: SMTP via `SMTP_HOST`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` (`SMTP_SECURITY=starttls|ssl|none`); see `.env.example`. Verified against a local SMTP server, not against a real provider (no credentials here). `MAIL_MODE=log` prints messages to the server log for local development.

## Limits and honest notes
- Demo authentication (one shared password, in-memory tokens) - **not production security**; no billing or multi-tenancy.
- Customers on a personal-account repo have write access (see above). Auto-update means Nexara changes what runs in a customer's Claude: agents are read-only, hooks are opt-in, every change is in `CHANGELOG.md`, and START-HERE explains how to pause updates.
- The agent's "don't open secret files" rule is an instruction, not a lock (once it ran a count-only search on `.env`); the plugin README ships a `permissions.deny` snippet for a hard block.
- Claude Code needs a paid Claude plan (Pro/Max/Team/Enterprise) or an Anthropic Console account; the free claude.ai plan does not include it.
- Deleting a customer repo is manual (the token needs `delete_repo`, which this app never asks for).
