---
id: 007
title: Site v3, public pages, contact, docs and spec-driven workflow
status: implemented
owner: saransh21122112
---

# 007 - Site v3: public pages, contact, docs, SDD, no dummy data

## Problem
- The public site looks cheap: a classic page scrollbar, emoji-free but plain cards, and on phones tags wrap one letter per line and tables squash (customer timeline screenshot).
- There is no About, What we do, How we work, Security, FAQ, Contact, Privacy or Terms page.
- The repo has no onboarding docs, no architecture/flow diagrams and no development rules, so contributors skip planning.
- The factory ships a fake sales/customers/orders dataset that specs, tests and the Playground lean on, which is wrong for real businesses.

## Goals / Non-goals
- Goal: a mobile-first, elegant, futuristic gold/maroon/white site with no visible page scrollbar on public pages and thin themed scrollbars elsewhere.
- Goal: public pages (About, What we do, How we work, Security, FAQ, Contact, Privacy, Terms) that reveal outcomes, never the technical mechanics.
- Goal: Contact form with stored messages (factory "Messages" view), abuse-limited.
- Goal: `CLAUDE.md` + `docs/` with architecture/flow diagrams; SDD as the enforced workflow (PR template + CI spec check).
- Goal: remove the sample dataset; the Playground becomes a Test lab that generates test cases with AI; optional "real examples" field on the request.
- Non-goal: CMS/blog, analytics or cookie banner, CAPTCHA, dark theme, translations, pricing page, publishing docs on the site.

## Behaviour (the contract)
1. Public routes `/about /what-we-do /how-we-work /security /faq /contact /privacy /terms` render server-side inside one shared layout (header, footer, meta tags). `/`, `/request`, `/verify`, `/track` use the same layout. Unknown paths return a styled 404. Every page, `/factory`, `sitemap.xml` and `robots.txt` also answer `HEAD` (hosting health probes use `HEAD /`).
2. `GET /sitemap.xml` lists public pages only; `GET /robots.txt` disallows `/factory /track /verify /api`; private pages carry `noindex`.
3. Legal wording is configured by `LEGAL_NAME`, `POLICY_EFFECTIVE` and `POLICY_REVIEWED` (unset values render nothing; until `POLICY_REVIEWED=true` the legal pages show a "draft pending legal review" note). The owner's contact details are written directly into the HTML (see spec 008).
4. Public text never contains internals (deny-list in tests).
5. `POST /api/contact` validates name, email, topic, message (length-capped), rejects honeypot or too-fast submissions with a generic success, limits per IP (default 5/hour) stores the message (hard cap 2000/day as a flood guard, so real visitors are not locked out), and emails the operator only when SMTP is configured and only for the first 100 messages a day. The anti-bot ticket is signed with a random per-process key that is not derived from any password. `GET /api/messages` and `POST /api/messages/{id}/read` are factory-only.
6. Page scrollbar is hidden on public pages with a thin reading-progress line instead; inner scroll areas and the factory use thin themed scrollbars.
7. Under 640px tables render as stacked cards, tags never wrap letter by letter, long values wrap, navigation is a sheet menu (public) or a scrollable pill bar (factory).
8. A strict Content-Security-Policy is sent; no public page needs inline scripts.
9. `lookup_business_data` and `data/sample_business_data.json` are removed; specs are generated without any assumed dataset and every test scenario carries its own inline synthetic input. Old stored specs that mention the tool still adapt and run.
10. `RequestIn.examples` (optional, <= 4000 chars) is stored, passed to spec/test generation, assistable.
11. Test lab: `POST /api/agents/{id}/generate-tests` returns validated extra scenarios (not saved until the user accepts); "Skip testing" shows how to add the delivered work to Claude from the private repo.
13. Public endpoints and messages never reveal configuration: `GET /api/health` returns only `{"status":"ok"}`, and the form assistant's "unavailable" and "busy" messages say nothing about keys, providers or errors (provider status stays staff-only at `/api/provider`).
14. An invalid `AI_PROVIDER` value is not silently ignored for staff: `/api/provider` returns a `warning` and the dashboard shows it (the app still falls back to `anthropic`).
12. `scripts/check_spec.py` fails a PR that changes `backend/` or `frontend/` without changing `docs/specs/`, unless the PR body contains `No-spec: <reason>`.

## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1-2 | pages, sitemap, robots, noindex, 404, HEAD requests | `tests/test_pages.py` (incl. `test_head_requests_work_on_every_page_because_hosting_health_probes_use_them`) |
| 3 | config-driven legal values and draft note | `tests/test_pages.py::test_legal_*` |
| 4 | deny-list scan of every public page | `tests/test_pages.py::test_public_pages_do_not_leak_internals` |
| 5 | contact validation, limits, storage, auth | `tests/test_contact.py` |
| 8 | CSP header, no inline scripts | `tests/test_pages.py::test_csp_*` |
| 9-11 | data policy, examples, generate-tests | `tests/test_data_policy.py` |
| 12 | spec check logic | `tests/test_sdd.py` |
| 14 | invalid provider value is flagged to staff | `tests/test_providers.py::test_an_invalid_ai_provider_value_is_reported_to_staff_not_silently_ignored` |
| 13 | no configuration leaks in public responses | `tests/test_api.py::test_public_health_is_just_ok`, `test_assist_unavailable_message_reveals_no_configuration`, `test_assist_upstream_failures_reveal_no_internals` |
| 6-7 | browser audit (overflow, scrollbars, phone) | manual per `docs/ui-quality.md`: 12 pages x 7 widths (320-1920) had no overflow, no visible page scrollbar, tap targets >= 32px, one h1; axe-core (WCAG A/AA/2.1AA + best-practice) clean on every public page and every dashboard view; no-JS, reduced-motion, 200% zoom, keyboard and menu behaviour checked; no console or CSP errors |
| docs | diagrams parse and stay in sync, specs well-formed, links resolve | `tests/test_docs.py` |

## Edge cases considered
Viewports 320 to 1920 and 200% zoom; always-visible OS scrollbars; touch vs mouse hover; reduced motion; no JavaScript; blocked fonts; iOS safe areas and input zoom; very long words, URLs and hashes; very long company names; empty/loading/error states; keyboard order and focus; tap targets; contrast; print for legal pages; anchor links under a sticky header; missing clipboard API; JS failure fallback; CSP compatibility.

## Security / privacy / public-content check
Contact is public: honeypot, time trap, per-IP and daily caps, size caps, output escaped, no auto-reply to strangers, no stored HTML rendered raw. Public pages obey the content rule. Fonts are self-hosted (no third-party request). No tracking cookies.

## Design notes
New `backend/pages.py` renderer, `frontend/pages/*.html` fragments, `contact_messages` table, shared `css/base.css`, `js/site.js`. Diagrams live in `docs/architecture.md` and `docs/flows.md`.

## Rollout
Optional env vars above (nothing breaks if unset). `data/sample_business_data.json` disappears; the tool is gone from new specs. Operators set legal values and `POLICY_REVIEWED=true` after a lawyer reviewed the drafts.
