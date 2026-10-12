---
id: 008
title: Owner profile and contact details written directly into the pages
status: implemented
owner: saransh21122112
---

# 008 - Owner profile and contact details (written directly in the HTML)

## Problem
Visitors cannot tell who runs Nexara or how to reach them. The owner supplied their resume and asked for their name, email, phone, LinkedIn and GitHub to appear on the site, **written directly into the HTML, with nothing to configure on Render**.

## Goals / Non-goals
- Goal: the owner's details are plain text in the page templates (`frontend/pages/*.html`, `frontend/layout.html`): Contact, About, footer, Privacy, Terms.
- Goal: no environment variable controls or can change them.
- Non-goal: copying employer names, project names, metrics or the technology stack from the resume (the public-content rule still applies). Non-goal: a street address.
- Superseded: the env-driven `CONTACT_EMAIL` / `OWNER_*` / `CONTACT_PHONE` / `LEGAL_ADDRESS` settings from the first version of this spec and from spec 007 behaviour 3. `LEGAL_NAME`, `POLICY_EFFECTIVE`, `POLICY_REVIEWED` stay as env settings.

## Behaviour
1. Contact page side card: email (`mailto:`), phone (`tel:`), location, LinkedIn and GitHub (external, `rel="noopener noreferrer"`).
2. About page: a "Who is behind Nexara" card: name, role, location, one outcome-only paragraph, links. No employer, project, framework, model or provider names.
3. Footer on every public page: email and LinkedIn. The phone number appears only on the Contact page.
4. Privacy and Terms state the service is run by the entity name and "operated by Saransh Gupta", Faridabad, Haryana, India, with the email.
5. Setting `CONTACT_EMAIL`, `CONTACT_PHONE`, `OWNER_*` or `LEGAL_ADDRESS` has no effect on any page.
6. New contact-form messages are emailed (only when SMTP is configured) to the same address, kept as the single constant `pages.NOTIFY_EMAIL`; a test fails if that constant and the address in the HTML ever differ.
## Acceptance criteria
| # | Criterion | Test |
|---|-----------|------|
| 1 | contact card contents and link attributes | `tests/test_pages.py::test_contact_page_shows_owner_card` |
| 2 | about card, content rule | `tests/test_pages.py::test_about_introduces_the_owner_without_internals` |
| 3 | footer on all pages, phone only on Contact | `tests/test_pages.py::test_footer_shows_email_and_linkedin_but_phone_only_on_contact` |
| 4 | legal pages name the operator | `tests/test_pages.py::test_legal_pages_name_the_operator` |
| 5 | env settings are ignored | `tests/test_pages.py::test_contact_details_are_in_the_html_and_ignore_environment_settings` |
| 6 | notify address matches the HTML | `tests/test_pages.py::test_notify_email_matches_the_html`, `tests/test_contact.py` |
## Edge cases considered
Very long name or location (wraps); phone digits normalised for `tel:` (written correctly in the HTML); a stale `CONTACT_EMAIL` still set on Render (ignored, so it cannot override the page); the number being scraped (accepted by the owner).
## Security / privacy / public-content check
This publishes the owner's personal phone and email on purpose, at their request. Changing or hiding them now means editing the HTML. The resume's technical detail stays out of public text.
## Rollout
No migration and nothing to set on Render. Remove any `CONTACT_EMAIL` env var from Render if one exists; it is no longer read.
