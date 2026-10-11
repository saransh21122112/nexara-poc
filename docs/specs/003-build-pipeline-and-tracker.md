---
id: 003
title: Live build pipeline and public tracker
status: implemented
owner: saransh21122112
---
# 003 - Build pipeline and tracker (retro-spec)
## Problem
Building takes about a minute with no feedback; staff and customers lose confidence.
## Behaviour
1. `POST /api/requests/{id}/build` (staff) runs received, analyse, specify (one model call), test (the spec's scenarios), package (file list only), ready. Every step is a recorded event; nothing is pushed to GitHub.
2. Failures stop the pipeline at the failing stage with the real reason; a server restart marks a running build `interrupted`.
3. `GET /api/builds/{id}` (staff) returns events and per-stage state; the Live build view draws it.
4. `GET /api/track/{id}` (public, rate-limited) returns five friendly steps and a pass count only: never company, spec text, run output or error text. Unknown references look identical.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1-3 | event order, failures, restart, auth | `tests/test_build.py` |
| 4 | tracker leaks nothing, limits, 404 parity | `tests/test_build.py::test_public_tracker_*` |
