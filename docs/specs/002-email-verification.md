---
id: 002
title: Email verification before delivery
status: implemented
owner: saransh21122112
---
# 002 - Email verification (retro-spec)
## Problem
The public form proves nothing about who is typing; without verification a stranger could attach requests to another company's repository.
## Behaviour
1. After a request, if verification is required, a one-time link `/verify#<token>` is emailed. The token is in the URL fragment (never sent to servers or logs), stored only as a hash, single-use, valid 48 hours.
2. Confirming needs a click (POST), so mail scanners that pre-open links cannot confirm.
3. Delivery is blocked for unverified requests unless staff record how they verified the person manually. `REQUIRE_EMAIL_VERIFICATION=false` explicitly opts out.
4. Resends are limited (cooldown and daily cap); the response never reveals whether mail is configured.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1-4 | token lifecycle, limits, delivery gate, manual verification | `tests/test_verification.py` |
