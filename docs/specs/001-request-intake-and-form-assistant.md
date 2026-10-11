---
id: 001
title: Request intake and the AI form assistant
status: implemented
owner: saransh21122112
---
# 001 - Request intake and form assistant (retro-spec)
## Problem
Customers must be able to ask for a plugin, skill, MCP server or tool without knowing our process, and typing seven boxes is a barrier.
## Behaviour
1. `POST /api/requests` accepts company, industry, kind (`Plugin|Skill|MCP server|Tool|Custom`), problem, tasks, tools, required output, optional real examples (<= 4000), contact email, GitHub username, Claude status, optional OS. Input is trimmed and length-capped; per-IP limit (default 20/hour).
2. `POST /api/requests/assist` fills or edits only the text boxes (never email, GitHub or OS) from a description or a per-box instruction; output is validated and whitelisted; limits: 10/hour/IP and 200/day, input <= 1500 chars; unavailable provider gives a friendly "fill it by hand".
3. Changed boxes are highlighted and each can be undone; nothing is submitted by the assistant.
4. Legacy stored kinds still display; retired kinds are refused on new input.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1 | validation, limits, kinds, legacy rows | `tests/test_api.py::test_request_flow_and_validation`, `test_request_kinds_and_legacy_rows` |
| 2-3 | whitelist, edit-one-box, limits, errors | `tests/test_api.py::test_assist_*` |
## Edge cases
Hostile text in any box (escaped on output); assistant output that is invalid JSON (502 with a human message); examples containing confidential data (warned in the UI and privacy notice).
