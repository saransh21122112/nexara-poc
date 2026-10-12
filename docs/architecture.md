# Architecture

Nexara POC is one Python web service (FastAPI) with a vanilla-JS front end, a SQL database, and a handful of outbound integrations. There is no build step and no background worker: long jobs run as asyncio tasks inside the web process.

## System context
```mermaid
flowchart LR
  visitor([Visitor / customer])
  staff([Nexara staff])
  subgraph nexara[Nexara web service]
    pub[Public site<br/>pages, request form, tracker, contact]
    fac[Staff dashboard /factory]
    api[JSON API /api]
  end
  db[(SQL database<br/>SQLite locally, Postgres on Render)]
  ai[[AI model provider<br/>switchable]]
  gh[[GitHub<br/>private repo per customer]]
  mail[[Email service<br/>optional SMTP]]
  cc([Customer's Claude Code])
  visitor --> pub --> api
  staff --> fac --> api
  api --> db
  api --> ai
  api --> gh
  api -.-> mail
  gh -->|clone / marketplace install / updates| cc
```

## Components
```mermaid
flowchart TB
  subgraph web[frontend: no build step]
    L[layout.html + pages/*.html<br/>server-rendered by pages.py]
    J[js: site, request, assist, track, contact, factory, live, changes, extras]
    C[css: base tokens, site, factory]
  end
  subgraph be[backend]
    M[main.py<br/>routes, auth, rate limits, CSP]
    PG[pages.py]
    AS[agent_service.py<br/>generate spec, revise, generate tests, evaluate, assist]
    BS[build_service.py<br/>live build pipeline]
    DS[delivery_service.py<br/>deliver, propose change, diff, refresh]
    RG[repo_gen.py + repo_templates.py<br/>plugin repo files]
    GC[github_client.py]
    PR[providers.py + subscription.py<br/>model switch]
    VF[verification.py + mailer.py]
    DB[database.py<br/>portability layer + all tables]
    TL[tools.py<br/>calculator for test runs]
  end
  web --> M
  M --> PG
  M --> AS --> PR
  M --> BS --> AS
  BS --> RG
  M --> DS --> RG
  DS --> GC
  M --> VF
  AS --> TL
  M & AS & BS & DS & VF --> DB
```

## Data model
Every table is plain SQL created by `database.init_db()`; the same SQL runs on SQLite and Postgres (`?` placeholders are rewritten for Postgres). JSON is stored as text.
```mermaid
erDiagram
  requests ||--o{ agents : "spec generated from"
  requests ||--o{ build_events : "live build log"
  requests ||--o{ email_verifications : "proves email"
  customers ||--o{ requests : "owns (customer_id inside data)"
  customers ||--o| repos : "one private repo"
  customers ||--o{ repo_events : "timeline"
  repos ||--o{ repo_agents : "contains"
  agents ||--o{ repo_agents : "delivered as"
  repos ||--o{ repo_changes : "dev branch + PR"
  agents ||--o{ runs : "test and run logs"
  agents ||--o{ eval_runs : "evaluations"
  requests { text id PK  text created_at  text data "form fields as JSON" }
  agents { text id PK  text request_id FK  text provider  text model  text spec "JSON spec" }
  customers { text id PK  text slug UK  text company_name  text contact_email  text github_username }
  repos { text id PK  text customer_id FK  text owner  text name  text state  text invite_status  text head_sha  text files "hashes of managed files" }
  repo_agents { text id PK  text repo_id FK  text agent_id FK  text plugin_slug  text spec_hash }
  repo_changes { text id PK  text repo_id FK  int pr_number  text state "open merged closed"  text files  text agents }
  repo_events { text id PK  text customer_id FK  text kind  text detail }
  build_events { text id PK  text request_id FK  int seq  text stage  text state  text detail }
  email_verifications { text id PK  text request_id FK  text token_hash UK  text expires_at  text used_at }
  contact_messages { text id PK  text name  text email  text topic  text message  int read }
  runs { text id PK  text agent_id FK  text kind  text task  text status  text response }
  eval_runs { text id PK  text agent_id FK  float pass_rate  text results }
```

## Deployment
```mermaid
flowchart LR
  dev[Developer] -->|PR to dev, then release PR to main| repo[(GitHub: nexara-poc)]
  repo -->|auto-deploy main| render[Render web service<br/>Docker image, non-root]
  render --> pg[(Render Postgres)]
  render --> gh[GitHub API<br/>customer repos]
  render --> ai[[AI provider]]
  render -.-> smtp[[SMTP]]
  user([Visitors and staff]) --> render
```
- One container, started by `Dockerfile`; `PORT` from the platform; `/api/health` is the health check.
- Env vars (see `.env.example`, `render.yaml`): `FACTORY_PASSWORD`, `AI_PROVIDER` plus a provider key or `CLAUDE_CODE_OAUTH_TOKEN`, `DATABASE_URL`, `GITHUB_TOKEN`, `GITHUB_OWNER`, `SMTP_*`, `REQUIRE_EMAIL_VERIFICATION`, `TRUSTED_PROXY_HOPS`, rate-limit knobs, and the legal/contact settings (`LEGAL_NAME`, `POLICY_EFFECTIVE`, `POLICY_REVIEWED`).
- Restarts interrupt a running build; startup marks it interrupted so the UI never spins forever.
- API docs (`/docs`) are disabled when `RENDER` is set.

## Cross-cutting rules
- Security headers and a strict CSP (`script-src 'self'`, no inline scripts) are added in `main.py`.
- Public endpoints are rate-limited per client IP (`limit()`); staff endpoints require the shared dashboard token (demo-grade auth, see `docs/flows.md` security model).
- All outward actions are confirmed by a human and logged as events.
