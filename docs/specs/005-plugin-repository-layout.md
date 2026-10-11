---
id: 005
title: The customer repository is one Claude Code plugin
status: implemented
owner: saransh21122112
---
# 005 - Plugin repository layout (retro-spec)
## Problem
Installing must be as easy as cloning one repository, and what we generate must be safe for the customer to run.
## Behaviour
1. The repo root is a plugin (`.claude-plugin/plugin.json`, `skills/<agent>/SKILL.md`, `agents/<agent>.md`, `custom/`, docs, `scripts/`), plus a marketplace manifest for one-command install with a `renames` map for older layouts.
2. Agents are read-only (`Read, Grep, Glob`). Text that mentions tools that do not exist in Claude Code is adapted.
3. Generated shell/PowerShell scripts embed only validated names; customer free text appears only in comments. Setup downloads the official Claude installer to a file, checks it is a script, shows its checksum, then runs it (after consent).
4. Generated files are tracked by blob hash so hand edits are detected; `custom/` is created once and left alone.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1-2 | layout, validation with the real CLI, adaptation | `tests/test_repo_gen.py` |
| 3 | scripts in a sandbox, hostile text only in comments | `tests/test_scripts.py`, `tests/test_repo_gen.py` |
| 4 | hand-edit detection, custom/ untouched | `tests/test_delivery.py` |
