---
description: Start a new spec from the template (spec-driven development)
argument-hint: <short-name>
---
Create a new spec for "$ARGUMENTS" following docs/sdd.md.

1. Find the next free number in docs/specs/ (look at the existing NNN-*.md files).
2. Copy docs/specs/000-template.md to docs/specs/NNN-<short-name>.md and set `id`, `title`, `status: draft`, `owner`.
3. Fill Problem, Goals / Non-goals, Behaviour, Acceptance criteria (one row per testable statement), Edge cases, Security / privacy / public-content check, Design notes and Rollout. Do not write any code yet.
4. Show me the spec and ask for approval. Only after I approve: set `status: approved`, write failing tests for each criterion, then implement, update the Mermaid diagrams in docs/ if flows or data changed, and open a PR to `dev`.
