# Spec-driven development (SDD) in this repo

Nobody writes code here before there is a written, reviewed spec. The spec is the source of truth; the code and tests prove it.

## The loop
1. **Branch** from `dev`: `git checkout dev && git pull && git checkout -b feat/<short-name>`. Never commit to `main` or `dev` directly.
2. **Spec**: copy `docs/specs/000-template.md` to `docs/specs/NNN-short-name.md` (next free number), fill it in, set `status: draft`. (In Claude Code: `/spec <short-name>`.)
3. **Review the spec** (a teammate, or the product owner in chat). Only when it is agreed set `status: approved`. Changing behaviour later means changing the spec first.
4. **Tests first**: turn each acceptance criterion into a failing test and put the test name in the spec table.
5. **Implement** the smallest change that makes the tests pass. Keep public text free of internals (see below).
6. **Update the docs**: any change to flows, data model or architecture updates the Mermaid diagrams in `docs/architecture.md` / `docs/flows.md` in the same PR.
7. **Verify** like a user: run the app, walk the flow, for UI changes screenshot desktop and phone and tick `docs/ui-quality.md`.
8. Set the spec `status: implemented`, open a **pull request to `dev`** using the template. CI must be green (backend tests on SQLite and Postgres, frontend browser tests, spec check).
9. A reviewer merges into `dev`. `dev` -> `main` is a separate, deliberate release PR.

## What counts as "needs a spec"
Any change under `backend/` or `frontend/` that alters behaviour, data, endpoints, text customers read, or look and feel. Exceptions that may skip the spec: typo fixes, dependency bumps, test-only changes, comment edits. Say so in the PR body with a line `No-spec: <reason>`; CI accepts that line and reviewers judge it.

## Definition of done
- Spec is `implemented` and matches what shipped.
- Every acceptance criterion has a passing test; the suite passes on SQLite and Postgres.
- Docs and diagrams updated; no secrets, no real customer data in the repo.
- UI work: checked against `docs/ui-quality.md` (phone width, scrollbars, keyboard, reduced motion).
- Public pages obey the content rule below.

## Public-content rule
Public pages say what customers get and why they can trust it. They never describe how Nexara builds it: no framework, library, model or provider names, no prompt or file-layout details, no internal workflow. A test scans public pages for a deny-list; add words there when you notice a leak.

## Non-negotiables that every spec must respect
- Draft, don't act: nothing is sent, published, merged or deployed on its own; outward actions need an explicit human confirmation.
- Secrets live in env/`.env`, never in the repo, docs, logs or screenshots.
- Customer data is untrusted input: escape on output, cap sizes, rate-limit public endpoints.
