# End-to-end flows

## 1. From a visitor's idea to a delivered plugin
```mermaid
sequenceDiagram
  autonumber
  actor V as Visitor
  participant S as Public site
  participant A as API
  participant AI as AI model
  participant DB as Database
  actor T as Staff
  participant GH as GitHub
  V->>S: describe the need (assistant optional)
  S->>A: POST /api/requests/assist
  A->>AI: fill or edit form boxes (data only, rate-limited)
  AI-->>S: suggested box values (human reviews)
  V->>S: submit form
  S->>A: POST /api/requests
  A->>DB: store request + customer match
  opt email verification on
    A-->>V: confirmation email, link with token in the URL fragment
    V->>A: POST /api/verify (click)
  end
  V->>S: /track?id=REQ-... (public progress)
  T->>A: Build and watch (POST /api/requests/id/build)
  A->>AI: write the specification
  A->>AI: run its test scenarios (each carries inline input)
  A->>DB: build_events at every step
  T->>A: Deliver (confirm=true)
  A->>GH: create private repo, commit plugin files, invite customer
  A->>DB: repos, repo_agents, repo_events
  V->>GH: accept invite, install with one command
```

## 2. Changing something already delivered (dev branch + pull request)
```mermaid
flowchart TD
  A[Staff: Change on an agent] --> B[Describe the change in plain words]
  B --> C[AI proposes an updated spec<br/>POST /agents/id/revise]
  C --> D{Review the spec diff}
  D -->|save| E[Spec updated, agent marked behind]
  E --> F[Review files<br/>GET /customers/id/diff]
  F --> G[Open pull request<br/>POST /customers/id/sync with confirm]
  G --> H[One Nexara commit on top of main -> branch dev]
  H --> I[Pull request dev to main opened or updated]
  I --> J{Person merges on GitHub}
  J -->|merged| K[Refresh from GitHub: record merge,<br/>agents now up to date]
  J -->|closed| L[Stays behind, can propose again]
  H -. dev has foreign commits .-> X[Refused unless overwrite_dev]
  G -. hand edits on main .-> Y[Conflict: confirm overwrite]
```

## 3. Live build pipeline
```mermaid
stateDiagram-v2
  [*] --> received
  received --> analyse
  analyse --> specify: model writes the spec
  specify --> test: scenarios run one by one
  test --> package: all checks passed
  package --> ready: files listed, nothing pushed
  specify --> failed: model or config error
  test --> failed: a check failed
  received --> interrupted: server restarted
  specify --> interrupted: server restarted
  test --> interrupted: server restarted
  ready --> [*]
  failed --> received: Build again
  interrupted --> received: Build again
```
The public tracker maps these to five friendly steps and exposes only step names and states.

## 4. Customer repository and invitation states
```mermaid
stateDiagram-v2
  [*] --> created: first delivery creates main directly
  created --> ready: files committed
  ready --> needs_retry: a push or invite failed
  needs_retry --> ready: retry continues, no duplicate repo
  state invite {
    [*] --> none
    none --> pending: invite sent
    pending --> accepted: customer accepts
    pending --> expired: 7 days pass
    expired --> pending: re-invite
  }
```

## 5. Pull-request change record
```mermaid
stateDiagram-v2
  [*] --> open: PR opened from dev
  open --> open: proposal updated, same PR
  open --> merged: refresh sees the merge, hashes recorded
  open --> closed: closed without merging
  closed --> [*]
  merged --> [*]
```

## 6. Public contact form
```mermaid
sequenceDiagram
  participant B as Browser
  participant A as API
  participant DB as Database
  B->>A: GET /api/contact/ticket (signed, timestamped)
  B->>A: POST /api/contact (fields + ticket + honeypot)
  alt honeypot filled or sent in under 2 seconds
    A-->>B: 201 ok (nothing stored, bot learns nothing)
  else bad or old ticket
    A-->>B: 400 reload the page
  else over rate limit
    A-->>B: 429
  else valid
    A->>DB: store message
    A-->>B: 201 ok (operator emailed only if SMTP is configured)
  end
```

## Security model (summary)
- **Who can do what.** Visitors: read pages, submit requests/contact, assist (rate-limited), track by reference (step names only). Staff (shared dashboard password -> in-memory token, lockout after repeated failures): everything else.
- **Outward actions need `confirm: true`:** create repo, push, invite, open PR, send verification email. Each is logged as an event.
- **Customer matching is strict** (company, GitHub user and email must all match) so a stranger cannot attach to someone else's repo; unverified email blocks delivery (unless explicitly disabled).
- **Untrusted text** never becomes executable generated code (only comments); user text is escaped on output; model output is validated against pydantic models and field whitelists.
- **Public text** never reveals internals; a test enforces it. A strict CSP and security headers are on every response.
- **Secrets** only in env; none in logs, events or docs.
