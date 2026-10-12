---
id: 008
title: Owner profile and contact details on the public site
status: implemented
owner: saransh21122112
---

# 008 - Owner profile and contact details

## Problem
The public pages show no real person or contact route unless env vars are set, so visitors cannot tell who runs Nexara or how to reach them. The owner supplied their resume and asked for their name, email, phone, LinkedIn and GitHub to be used where it makes sense.

## Goals / Non-goals
- Goal: sensible defaults for the owner's details (name, role, location, email, phone, LinkedIn, GitHub) shown on Contact, About, the footer and the legal pages; every value overridable or removable through env.
- Goal: the About page introduces the person in outcome terms only.
- Non-goal: copying employer names, project names, metrics or the technology stack from the resume. The public-content rule still applies.
- Non-goal: street address; only city, state and country.

## Behaviour
1. Defaults (from the resume) apply only when an env var is absent: `CONTACT_EMAIL`, `CONTACT_PHONE`, `OWNER_NAME`, `OWNER_ROLE`, `OWNER_LOCATION`, `OWNER_LINKEDIN`, `OWNER_GITHUB`. A var set to an empty string hides that item.
2. Contact page side card: email (`mailto:`), phone (`tel:` with digits only), location, LinkedIn and GitHub (external links, `rel="noopener noreferrer"`).
3. About page: a "Who is behind Nexara" card with name, role, one outcome-focused paragraph, location and profile links. No employer, project, framework, model or provider names.
4. Footer on every public page: email and LinkedIn. The phone number appears only on the Contact page.
5. Privacy and Terms say the service is run by the entity name and, when set, "operated by <owner name>", plus the location.
6. All values are HTML-escaped; link URLs must start with `https://` or they are dropped.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1 | defaults present, env overrides, empty hides | `tests/test_pages.py::test_owner_details_defaults_override_and_hide` |
| 2 | contact card links | `tests/test_pages.py::test_contact_page_shows_owner_card` |
| 3 | about card, content rule | `tests/test_pages.py::test_about_introduces_the_owner_without_internals` |
| 4 | footer and phone placement | `tests/test_pages.py::test_footer_shows_email_and_linkedin_but_phone_only_on_contact` |
| 5 | legal pages | `tests/test_pages.py::test_legal_pages_name_the_operator` |
| 6 | escaping and URL scheme | `tests/test_pages.py::test_owner_values_are_escaped_and_only_https_links_survive` |
## Edge cases considered
Very long name or location (wraps); phone with spaces or punctuation (normalised for `tel:`); all values hidden (cards disappear cleanly, no empty boxes); a hostile env value; phone number exposed to scrapers (accepted by the owner, easy to hide with `CONTACT_PHONE=`).
## Security / privacy / public-content check
This publishes the owner's personal phone and email on purpose, at their request. Hide either with an empty env var. The resume's technical detail stays out of public text.
## Rollout
No migration. Set the env vars on Render only to change or hide a value.
