# Customer experience verification | 1 October 2026

## Results

| Check | Observed result |
| --- | --- |
| Uploaded project baseline | 284 tests passed |
| Provider and invoice focused regression | 47 tests passed after provider separation |
| Expanded backend suite | 325 tests passed; final repeat recorded in evidence/backend-tests.txt |
| New backend regression coverage | 41 tests added |
| Real API customer experience browser | Passed; 10 grouped workflow checks, 21 route/viewport checks, no page errors or native dialogs |
| Existing real API invoice browser | Passed; owner signing, PDF retrieval, outbox code, customer signing and completed job |
| Existing offline demo browser | Passed; simulated invoice signing and 16 route/viewport checks |
| Expanded offline demo browser | Passed; city editing, private conversation simulation, signup exit behavior and 33 route/viewport checks |
| Display copy audit | No hyphenated display words on the 11 audited demo routes after cleanup; technical identifiers and user supplied data are not rewritten |
| Responsive layouts | No page level horizontal overflow in the 70 combined route/viewport checks; tested widths 320, 390 and 1440 where recorded |
| Syntax checks | Python compilation, all application/adapter JavaScript files and the two shell launch scripts passed |
| Dependency version gate | **Failed in the local environment**; five installed library versions differ from the inherited deployment pins |
| Nationwide registry installation | **Not completed here**; the bundled file is still the marked 20 ZIP, 13 city development fixture |

Individual test output and machine readable browser summaries are in `docs/evidence/`. Results are local, synthetic and repeatable; no live customer data or provider secrets are bundled.

## Important failure cases exercised

The expanded tests verify tenant isolation for booking and quote conversations, custom domain boundaries, role rejection, message limits/pagination, idempotent sends, private note revision conflicts, review eligibility and all valid ratings. Quote request retries do not duplicate requests. Invoice references remain immutable, and customer proof images do not mark payments received.

City validation rejects unknown cities, duplicate cities, empty selected coverage, ZIPs assigned to another city, invalid/non-US ZIPs and unsupported customer locations. Leading zeroes survive. The server duration test verifies the booking end and travel buffer, not only a displayed label. Upgrade tests retain photos and existing bookings while requiring legacy published pages to review their city coverage.

Square checks include multiple eligible locations, encrypted pending tokens, location expiry and preservation of the prior connection when a different account/location would orphan payment history. Paddle checks include provider trial reconciliation, exact customer/subscription ownership and no invented subscribed state for an unpurchased free trial.

The real API browser test simulates losing a successful photo upload response after commit, then retries. It proves that only one booking and one photo exist. It also checks normal and heavy quote ZIP rejection, escaped chat text, unsent message protection, private notes, review submission, a confirmed Venmo receipt and proof remaining separate from payment status.

The first broad regression run caught two public branding title regressions. They were fixed, individually retested and covered by the passing full suites. A browser check exposed the quote button being enabled after rendering an invalid ZIP; its render and action checks were corrected. A custom-domain assertion was corrected to test `no-store` semantics rather than require an unnecessary exact header string. The demo test was adapted to its existing strict CSP rather than weakening that policy.

## Browser scope and limitations

`tests/browser_support.py` executes the real application UI through a TestClient bridge into actual FastAPI handlers and a temporary SQLite database. It does not emulate a full deployed HTTPS origin, real cookie storage, provider pages or a reverse proxy. The static preview exercises its isolated in-memory adapter with its CSP intact. The explicit exit has the correct real destination and is not canceled by application guards; the test cancels actual navigation. Successful navigation to a public live website was not certified.

The viewport checks use Chromium, not physical iOS/Android devices. Before/after capture controls exist, but actual cameras, Safari behavior, accessibility conformance, screen readers and slower real networks still need acceptance checks. A message stored in the outbox is not proof of email delivery.

## Local stack and deployment gap

Local execution used Python 3.13.5, FastAPI 0.128.2, Starlette 0.50.0, cryptography 46.0.4, Pillow 12.3.0, python-multipart 0.0.29, Pydantic 2.13.4, ReportLab 4.4.9, pytest 9.0.2, Playwright 1.57.0, Uvicorn 0.48.0 and HTTPX 0.28.1.

The inherited production requirements call for FastAPI 0.141.1, Starlette 1.6.0, cryptography 50.0.1, python-multipart 0.0.32 and ReportLab 5.0.1. Those pins were not downgraded to force the version gate to pass. The exact pinned installation and an online vulnerability audit were not completed in this network-restricted environment. Install and rerun all checks on the real deployment artifact before public launch.

No new runtime dependency or framework was introduced. The production frontend JavaScript plus CSS is approximately 276 KB uncompressed and 82 KB when those files are individually gzip compressed in this environment. These are file measurements, not a measured mobile performance score, load test or latency promise. Existing response compression remains enabled.

The nationwide GeoNames download could not be reached here because outbound DNS resolution failed. The installer supports direct download or a supplied original US.zip file and validates coverage before atomic replacement. Production refuses the fixture. Nationwide data freshness and complete coverage are not verified until the full dataset is installed and tested.

## Not performed

Live Square/Paddle transactions, real merchant OAuth, external email delivery/receiving, production deployment, DNS/TLS changes, a clean pinned dependency build or online advisory audit, Docker/Windows execution, full nationwide data installation, physical camera tests, load/penetration testing, independent legal review or a production-volume restore. No claim of zero faults, guaranteed security, legal compliance or production readiness is made.

## Reproduce

```sh
python -m pip install -r requirements-dev.txt
python -m pip check
python scripts/verify_dependencies.py
python -m pytest -q
python -m compileall -q server scripts tests
python scripts/build_preview.py
python tests/browser_workflow.py --chromium /path/to/chromium --output ./browser-evidence
python tests/browser_experience.py --chromium /path/to/chromium --output ./browser-evidence
python tests/preview_workflow.py --chromium /path/to/chromium --output ./browser-evidence
python tests/preview_experience.py --chromium /path/to/chromium --output ./browser-evidence
```

Also run `node --check` on every `web/assets/*.js` and `preview/*.js` file and `sh -n setup.sh run.sh`. Node is needed only for those development checks. Follow LAUNCH_CHECKLIST.md for deployment, policies, providers and upgrade review.
