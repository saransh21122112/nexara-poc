---
id: NNN
title: Short imperative title
status: draft          # draft -> approved -> implemented
owner: your-github-handle
---

# NNN - Title

## Problem
What is wrong or missing, for whom, and how we know (a screenshot, a bug, a user request). No solution yet.

## Goals / Non-goals
- Goal: ...
- Non-goal: ... (what we are deliberately not doing)

## Behaviour (the contract)
Plain statements of what the system does, written so a test can check them. Include error cases, limits, empty states, who is allowed.

## Acceptance criteria
Each line becomes at least one test. Reference the test once it exists.
| # | Criterion | Test |
|---|-----------|------|
| 1 | ... | `tests/test_x.py::test_name` |

## Edge cases considered
List them, even the ones you decided not to handle (and why).

## Security / privacy / public-content check
Who can call this, what is stored, what could leak, does any public text reveal how Nexara works internally?

## Design notes
Only what a reader needs: data shape changes, new endpoints, diagrams to update in `docs/`.

## Rollout
Env vars, migrations, what the operator must do, how to roll back.
