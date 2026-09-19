# Repair verification - 19 September 2026

## Results

| Check | Result |
| --- | --- |
| Uploaded baseline | 271 tests passed |
| Final backend suite | **284 passed in 42.81 seconds**, no skips |
| Python compilation | Passed for server, scripts and tests |
| JavaScript syntax | Passed for every web asset and preview adapter |
| Shell syntax | setup.sh and run.sh passed |
| Local email using the supplied .env | All three channels produced .eml files with the correct getkontracts.app sender and support Reply-To; no external mail sent |
| Real-handler browser flow | Business contact link, detailer signing, PDF retrieval, email code, customer signing and Completed booking passed; no JavaScript page errors |
| Offline preview browser flow | Simulated invoice signing passed; no external HTTP(S) requests or JavaScript page errors |
| Responsive preview | Eight routes at 1440px and 390px; no horizontal page overflow |

The 13 added regression cases cover the four new-domain identities, environment precedence/UTF-8 configuration, stable relative data/database paths, legacy sender compatibility, mixed-domain rejection, disabled production-mail rejection, the production origin, one email template, accurate delivery modes and validated business contact addresses.

The browser contact regression initially exposed the missing business email in the booking-management API. Both that API and its contact link were corrected, then the flow and full test suite passed.

## Scope

The final preview was rebuilt from the updated UI. Browser checks use Chromium with a TestClient bridge for real HTTP handlers and a temporary SQLite database; the offline preview uses its isolated simulation. These checks do not verify a deployed reverse proxy, actual browser cookies/CSP over HTTPS, device cameras or external checkout.

Local test stack: Python 3.13.5, FastAPI 0.128.2, Starlette 0.50.0, cryptography 46.0.4, python-multipart 0.0.29, ReportLab 4.4.9, pytest 9.0.2 and Playwright 1.57.0. This is **not** the production dependency stack. The pinned clean installation was attempted in an isolated environment but the package resolver returned no matching distribution for fastapi==0.141.1. Production pins were not downgraded to make tests pass. Install and test the exact requirements.txt stack on the deployment host and run an online dependency audit.

Not performed: live Resend/SMTP delivery, receiving-inbox/forwarding tests, DNS changes, deployment, HTTPS certificate checks, real Paddle/Square transactions, Windows execution, Docker build, full nationwide ZIP download, penetration/load testing, a production-volume restore or legal review. No live account credentials were supplied. No claim of perfect security, live email delivery or production readiness is made.

## What the archive contains

Application source, the regenerated demo, launch/deployment files, focused documentation and regression tests. No Git history, old patch installer/ZIP, patch backups, duplicate email templates, caches, generated secrets, test databases, screenshots or stale checksum manifest are included. Existing customer records and signed invoices were not rewritten.

## Reproduce

```sh
python -m pip install -r requirements-dev.txt
python -m pip check
python scripts/verify_dependencies.py
python -m pytest -q
python -m compileall -q server scripts tests
python scripts/email_admin.py check
python tests/browser_workflow.py --chromium /path/to/chromium --output ./browser-evidence
python tests/preview_workflow.py --chromium /path/to/chromium --output ./browser-evidence
```

`node --check` is useful for development syntax checks but Node is not an application dependency. Rebuild the offline preview with `python scripts/build_preview.py` after installing development requirements. Deployment gates are in DEPLOYMENT.md.
