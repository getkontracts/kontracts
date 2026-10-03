# Launch checklist

## Required configuration still to supply or verify

1. **Nationwide city and ZIP data.** Run `python scripts/update_zipcodes.py` on a networked host, or import an original GeoNames `US.zip` with `--input`. Check the installed code/city counts, source metadata and coverage of all 50 states plus DC, then restart. The bundled sample is not nationwide data. Tests against the full registry remain an acceptance step.
2. **Your Square referral address.** Set `SQUARE_REFERRAL_URL` to the actual HTTPS affiliate URL. Do not use it as an OAuth or webhook URL. Keep existing Square authorization and webhook configuration separate.
3. **Provider acceptance.** Test Square authorization with one and multiple locations, reconnect, deposit checkout, duplicate events, refunds and invoice balances. Test Paddle checkout, confirmation delay, provider trial, active status, cancellation and portal access. Use isolated sandbox accounts first, then a controlled production acceptance test after approval. No live provider tests were performed here.
4. **Production dependency build.** Install the existing pinned requirements in a clean Python 3.13 environment. Run dependency validation, the full test suite and an online advisory audit on that exact deployment artifact. The local test environment does not match every production pin.
5. **Policies, delivery and deployment.** Supply reviewed business identity and production terms/privacy files. Ensure those operator supplied files also describe only the single public plan and cover private conversations, notes, photos, reviews and payment proof. Configure and verify Resend or SMTP and a receiving support inbox. Test HTTPS, cookies, CSRF, the reverse proxy, persistent private storage and a full restore.

These are launch gates, not evidence of a deployed integration or a legal compliance certification.

## Upgrade an existing installation

Stop the application. Back up the database, all retained files and matching secret using the existing backup procedure. Test a restore in isolation. Preserve `.env`, `APP_SECRET` and data; this archive does not contain them. Merge the optional referral variable into the existing environment rather than replacing the entire file.

Deploy the source, install dependencies, import the full postal dataset and restart once to apply migrations. Review any business with the `service_area.review_required` audit entry. Its old ZIP list is preserved, but its public page is unpublished until it selects cities, saves coverage and republishes. Private bookings, invoices and conversations remain accessible through their existing links.

Keep one application process/replica. Shared SQLite storage with multiple independent app processes is not a supported way to scale this release's in-process payment locks.

## Client acceptance walkthrough

Create a synthetic detailer. Verify email. Set business profile, service durations, weekly hours, buffer and policy. Select a city, choose one ZIP, and prove a different ZIP is rejected without creating a request or payment. Repeat with citywide coverage and a leading zero ZIP in its correct state.

Submit a booking with and without photos. Interrupt an upload and retry; verify only one booking/photo batch exists. Ask for details while approval is pending, reply from the private customer link, and check another business cannot read it. Repeat for a quote before an offer is made.

Approve, perform and document a job with before/after photos. Complete invoice signing. Request and submit a review. Verify private notes do not appear on customer pages. Record a genuinely received direct payment; uploading a proof image alone must leave its invoice unpaid. Verify Square records cannot be marked paid using the direct receipt control.

Open the demo and navigate back to real signup. Check narrow mobile portrait, landscape, keyboard focus, screen reader basics, iOS Safari and Android camera/gallery behavior on physical devices. The supplied Chromium viewport checks are not a substitute for those device tests.
