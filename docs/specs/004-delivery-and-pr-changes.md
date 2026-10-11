---
id: 004
title: Delivery to a private repository and changes by pull request
status: implemented
owner: saransh21122112
---
# 004 - Delivery and pull-request changes (retro-spec)
## Problem
Customers need the result in their own private place, and later changes must be reviewable, not pushed behind their back.
## Behaviour
1. First delivery (staff, `confirm`) creates a private repository `nexara-<company>`, commits the plugin files to `main`, and invites the customer's GitHub user (only an established, matching user).
2. Any later change (more agents, edited spec, Sync) is one Nexara commit on top of `main`, placed on branch `dev`, with a pull request `dev` -> `main` opened or updated. Nexara never merges.
3. `dev` with commits Nexara did not make is refused unless `overwrite_dev`. Hand edits on `main` conflict unless `overwrite_conflicts`. `custom/` is never touched.
4. `refresh` records a merge (hashes, agent versions) or a close; PR permission errors explain the token scope needed.
5. Staff can revise a spec in plain words (AI proposes, a diff is shown, nothing saved until accepted) and review per-file diffs before opening the PR.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1 | first delivery, invite rules, isolation | `tests/test_delivery.py` |
| 2-5 | dev branch, PR lifecycle, conflicts, revise, diff | `tests/test_changes.py` |
