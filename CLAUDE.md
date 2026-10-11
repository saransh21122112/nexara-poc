# Nexara AI POC - guide for Claude Code and contributors

Nexara builds **plugins, skills, MCP servers and tools** for businesses. A public site takes requests; a password-protected staff dashboard (`/factory`) turns each request into a specification with an AI model, tests it, and delivers it to the customer as a **private GitHub repository** that is one Claude Code plugin. Later changes reach the customer as a pull request from branch `dev` into `main` of that repository.

Read `docs/README.md` first. Architecture and flow diagrams are in `docs/architecture.md` and `docs/flows.md`.

## The rule: spec-driven development (SDD)
Nothing under `backend/` or `frontend/` changes before there is an agreed spec in `docs/specs/`.
1. `git checkout dev && git pull && git checkout -b feat/<short-name>` (never commit to `main` or `dev` directly).
2. Write `docs/specs/NNN-<name>.md` from `docs/specs/000-template.md` (or run `/spec <name>`). Get it agreed, then set `status: approved`.
3. Write failing tests for each acceptance criterion, then implement, then update the Mermaid diagrams in `docs/` if flows, data or architecture changed.
4. Run the app and use the feature like a user. For UI, check `docs/ui-quality.md` (phone width, scrollbars, keyboard, reduced motion).
5. Set the spec `status: implemented` and open a **pull request to `dev`** using the template. CI must pass (backend tests on SQLite and Postgres, frontend tests, spec check).
Typos, dependency bumps and test-only changes may skip the spec with a `No-spec: <reason>` line in the PR description. Full detail: `docs/sdd.md`.

## Non-negotiables
- **Draft, don't act.** Nothing is sent, published, merged or deployed on its own; outward actions need an explicit human confirmation (`confirm: true` in the API, a confirm bar in the UI).
- **No secrets in the repo, docs, logs or screenshots.** Config lives in env / `.env` (see `.env.example`). Never commit `.env`.
- **Public content rule.** Public pages describe outcomes, never mechanics: no framework, library, model or provider names, no internal workflow. `tests/test_pages.py` scans for a deny-list; extend it when you spot a leak.
- **Customer input is untrusted.** Escape on output, cap sizes, rate-limit public endpoints, never put free text into executable generated files (only comments).
- **No dummy data.** There is no sample dataset; specs and tests use the customer's own examples or inline synthetic inputs.

## Commands
```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # set FACTORY_PASSWORD, a provider, optional GITHUB_*, SMTP_*
.venv/bin/uvicorn backend.main:app --reload --port 8000    # site at /, staff dashboard at /factory
.venv/bin/python -m pytest -q tests -m "not ui"              # backend tests, SQLite (default)
.venv/bin/python -m playwright install chromium            # once, for the browser tests
.venv/bin/python -m pytest -q tests -m ui                  # frontend tests: real browser, phone/desktop, accessibility
TEST_DATABASE_URL=postgresql://user:pass@localhost:5433/db .venv/bin/python -m pytest -q tests   # Postgres
python scripts/build_diagrams.py          # regenerate docs/diagrams/architecture.html after editing diagrams in docs/*.md
python scripts/check_spec.py              # the spec gate CI runs (needs BASE_REF, default origin/dev)
python -m tests.fake_github --port 9912 --token fake-token --owner demo-owner   # fake GitHub for local end-to-end runs
```

## Layout
- `backend/main.py` routes and middleware (security headers, CSP); `pages.py` server-rendered public pages; `agent_service.py` spec generation, tests, revision; `build_service.py` live build pipeline; `delivery_service.py` + `github_client.py` + `repo_gen.py` + `repo_templates.py` the customer repository and its pull-request changes; `database.py` SQLite/Postgres portability layer and all tables; `verification.py` + `mailer.py` email; `providers.py` + `subscription.py` the model switch.
- `frontend/` `layout.html` + `pages/*.html` (public templates), `css/` (`base.css` tokens, `site.css`, `factory.css`), `js/` (vanilla JS, no build step), `fonts/` (self-hosted), `factory.html` (staff dashboard shell).
- `tests/` pytest suite; `tests/fake_github.py` is an in-memory GitHub used by delivery tests.
- `docs/` diagrams, specs, workflow. `scripts/` repo tooling. `.github/` PR template and CI.

## Conventions
- Python: small functions, plain SQL through `database.py` helpers, no ORM. JS: vanilla, no framework, no inline scripts (CSP), escape everything you put in `innerHTML` with `esc()`.
- Design tokens only from `frontend/css/base.css` (maroon, gold, ivory). Icons come from `frontend/js/icons.js` (single source, also inlined server-side).
- Every public endpoint is rate-limited with `limit()` in `main.py`; every staff endpoint depends on `factory_auth`.
