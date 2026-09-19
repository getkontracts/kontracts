# Security review and release limitations

Reviewed 17 September 2026. Scope: uploaded source, application trust boundaries, SQLite schema/access, auth and tenant checks, booking approval, billing, invoice signatures/payments, uploads, demo isolation, deployment files and local automated/browser/PDF tests. **This is not a penetration-test certificate, proof of zero vulnerabilities, independent legal review or verification of the eventual hosting environment.**

## Material changes

| Area | Change and evidence | Remaining responsibility |
|---|---|---|
| Request boundary | Host/path validation, HTTP method allowlist, bounded bodies/headers/query, rejection of URL-encoded forms, single-range enforcement, private-file/source paths denied. Existing same-origin/CSRF/session defenses retained. | Test real reverse-proxy behavior; network-level DoS protection remains external. |
| Dependency vulnerabilities | Deployment pins updated to FastAPI 0.141.1, Starlette 1.6.0, cryptography 50.0.1, multipart 0.0.32, ReportLab 5.0.1. Production startup rejects older key security libraries. | **Updated stack was NOT installed or vulnerability-scanned here.** Online clean installation, regression testing and advisory review are release gates. |
| Tenant access | Invoice/bookings/uploads/exports use authenticated shop ownership; opaque public tokens are hashed, high-entropy and independently checked. Cross-tenant invoice access tests reject other owners. | Root/database administrators are trusted; this is not protection from a compromised host. |
| Database | Parameterized queries, foreign keys, transactions, private data-directory/file permissions, secure-delete setting, trusted-schema disabled. | SQLite booking/customer/outbox data is not fully encrypted. Use an encrypted volume and encrypted off-host backups. SQLite deletion/WAL behavior is not certified secure erasure. |
| Stored secrets and invoices | Provider/bearer secrets and invoice payload/PDF use application encryption; SHA-256/HMAC checks detect changed retained documents. Immutable SQL triggers protect versions/receipts/adjustments. | APP_SECRET is a critical root secret. HMAC is not public-key notarization or protection from an attacker with the application secret. |
| Signatures | Authenticated detailer intent; customer PDF access, email OTP, explicit consent, document hash, name/time/audit fingerprints; attempt/expiry checks. Job completes only after customer signing. | Email access is not civil-identity verification. No certificate-based/PAdES signature or legal enforceability guarantee. |
| Approval | Pending requests cannot charge or confirm; transactional approval rechecks slot conflicts. Parallel competing approvals tested. | A pending request does not reserve the calendar; client copy explains this. |
| Payments | Own-merchant Square checkout; no platform application fee; signed webhooks/authoritative reconciliation; separate immutable receipt/refund records; frozen checkout amount survives later deposit refunds. | Live provider flows were mocked. Refund/chargeback resolution, old external checkout-link cancellation and unusual split/tip/manual payments need merchant review. |
| US geography | ZIP membership, ASCII five digits, US country, matching state and service area; production refuses fixture registry. | Full data must be installed and periodically refreshed. It does not verify streets, residency or IP location; GeoNames is not USPS address certification. |
| Email | Three outbound identities on getkontracts.app, separate support inbox, purpose-specific Reply-To, queued/deduplicated notifications. Demo mail suppressed. | Provision and monitor the receiving inbox; configure sender verification, SPF/DKIM/DMARC. Delivery failures/bounces need monitoring. |
| Uploads | Existing image decode/re-encode, metadata removal, pixel/size/quota checks and private authorization retained. | Storage capacity and modern device-camera behavior need deployment checks. |
| Demo | In-memory fake data; no network fallback, real exports, emails or payments; connect-src none; server demo write denial. | Browser-only source can be copied or modified. No client-only demo can prevent someone rewriting its code; this demo has no live credentials or order backend. |

## Verification

See `TEST_REPORT.md` for the repair verification scope and dependency limitations. Passing local tests does not establish production security or live provider delivery.

## Explicit blockers before public launch

1. Install the exact deployment requirements in a clean Python 3.13 environment, run pip check, the dependency version gate, the entire test suite and an online dependency/advisory audit. The older evaluated stack contains published advisories and is development-only.
2. Download/validate the nationwide ZIP registry; the shipped 20-code development fixture must not be used for real nationwide bookings. Production refuses it.
3. Configure real HTTPS and secrets, reviewed policies, encrypted storage and tested off-host backups. Keep one process/replica; do not expose the app port directly.
4. Verify Paddle onboarding/price/webhooks, the sender and receiving inbox, and optional Square OAuth/payment/refund webhooks with sandbox then a controlled production acceptance check. No such external account test was performed here.
5. Establish a retention/correction/export/deletion policy, operational monitoring and incident process. Retained signed records are append-only through the normal API, not automatically purged. Have a qualified reviewer assess the e-signature consent process and applicable tax/invoice requirements.

## Residual limits

There is no mandatory owner MFA in the standard password flow, no external identity verification, no centralized inbound-support inbox UI, no automatic chargeback adjudication and no whole-database application encryption. Use unique strong passwords and secure the owner mailbox; evaluate an MFA feature before higher-risk deployments. Single-process rate limiting and a single SQLite writer are appropriate only to the intended small deployment, not unlimited scale. Certificate identity signing, immutable external/WORM storage, independent penetration testing and load testing were not implemented or performed.

Default PDF typography uses standard PDF fonts. The English sample was verified; non-Latin names, accessibility/tagged-PDF compliance and assistive-technology reading order need additional acceptance work. Do not claim universal script or accessibility certification.

## Primary security references

- Starlette Host/path advisory: https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr
- Starlette malformed-path advisory: https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3
- Starlette form-limit advisory: https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq
- Current Starlette releases: https://pypi.org/project/starlette/
- Cryptography security fixes: https://cryptography.io/en/latest/changelog/
- ReportLab security defaults: https://docs.reportlab.com/releases/notes/whats-new-50/
- OWASP authorization: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- OWASP CSRF: https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OWASP SQL injection: https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- US electronic-signature statute: https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title15-section7001&num=0&edition=prelim
- Postal dataset/attribution: https://download.geonames.org/export/zip/
