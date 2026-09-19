# Deployment - complete the gates before accepting clients

Target: Python 3.13, one process/replica, SQLite on a persistent encrypted volume, HTTPS reverse proxy, outbound access to the configured email/payment APIs. The single SQLite writer and in-process synchronization are deliberate; do not scale horizontally without redesigning them.

## 1. Clean installation and release validation

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
python scripts/verify_dependencies.py
python -m pytest -q
python -m pip install pip-audit
python -m pip_audit -r requirements.txt
python scripts/update_zipcodes.py
```

The audit consults public package/advisory services. Review all findings; do not ignore them or blindly force upgrades. Record a fully resolved lock file and deployment image digest after the clean build passes, then repeat on that artifact. See TEST_REPORT.md for the exact checks completed. A clean deployment-pin installation and online vulnerability audit remain release gates.

The ZIP installer obtains GeoNames US data, validates count/states/size and writes an atomic registry with source/license metadata. Production rejects the bundled fixture. Run it periodically as a controlled maintenance step, review changes and rebuild/restart. The postal scope is 50 states + DC, not territories/military ZIPs; no runtime key or per-booking API is needed. Source: https://download.geonames.org/export/zip/ (CC BY 4.0 attribution is retained).

## 2. Minimal .env

The included .env and .env.example are identical clean placeholders. Keep real .env outside version control. Use APP_ENV=production, BASE_URL=https://getkontracts.app, stable random APP_SECRET (at least 40 characters), and a private persistent DATA_DIR. Generate a new installation's secret with:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never regenerate the secret on an existing installation without a planned cryptographic migration. The supplied file contains no live credentials.

Mail: use the four identities in .env.example on getkontracts.app and MAIL_ENABLED=true. Set RESEND_API_KEY after verifying the sending domain; alternatively configure authenticated TLS SMTP, not both. Configure a receiving inbox or forwarding for support@getkontracts.app. Customer booking replies go to the detailer's contact email; supported owner notices reply to the customer. Account/subscription replies go to support. See EMAIL_SETUP.md for the exact mapping, checks and safe retry procedure. Provider-generated Square/Paddle receipts use their own sender settings.

Subscription: set PADDLE_ENV=production, PADDLE_API_KEY, PADDLE_CLIENT_TOKEN, PADDLE_WEBHOOK_SECRET and one PADDLE_PRICE_ID. Configure an active USD49 monthly price without a provider-side trial or alternate tiers; the app validates the price server-side. Complete Paddle business/product approval independently. A syntactically configured key is not provider acceptance.

Square is optional: leave its three credentials blank for an entirely in-person deployment. For online payments, set SQUARE_ENV=production, SQUARE_APPLICATION_ID, SQUARE_APPLICATION_SECRET and SQUARE_WEBHOOK_SIGNATURE_KEY. Each US detailer authorizes their own Square merchant account; no central platform collection account or application fee is added. Subscription money still uses Paddle even when job payments are in person. API version defaults to 2026-08-19.

Policies: set LEGAL_NAME, LEGAL_ADDRESS, LEGAL_TERMS_PATH, LEGAL_PRIVACY_PATH and LEGAL_REVIEW_CONFIRMED after actual review. See LEGAL_CHECKLIST.md. Sandbox credentials are refused in production; use a separate isolated staging deployment for sandbox testing.

## 3. Exact provider endpoints

Append these paths to your canonical BASE_URL. Do not guess the host from request headers or use an untrusted redirect.

- Square OAuth callback: `/api/integrations/square/callback`
- Square webhook: `/api/webhooks/square`
- Paddle webhook: `/api/webhooks/paddle`

Confirm the callback paths against `server/app.py` when configuring providers, subscribe to the payment/refund and subscription/transaction events used by the handler, and ensure signature verification uses the exact public webhook URL. Keep webhook secrets distinct from API keys. Test merchant, currency, amount, event replay and customer/subscription ownership checks. Old Google authentication is compatibility-only and not part of the minimal new setup.

## 4. Deployment choices

Linux/Docker Compose: fill .env, provide the reviewed `legal/terms.txt` and `legal/privacy.txt`, point DNS to the server, then `docker compose up --build -d`. Only Caddy publishes ports 80/443; the app stays on the internal network. The app runs as UID/GID10001 with dropped capabilities. Trusting proxy headers is safe only while the internal app port cannot be reached directly. Back up named volumes and use encrypted host storage. Pin reviewed base/Caddy image digests for your release instead of treating mutable tags as reproducible artifacts.

For a managed container host: use the Dockerfile, one replica, persistent `/data`, DATA_DIR=/data, runtime PORT and `/api/health`. Provision private UID10001-writable storage and configure trusted proxies deliberately. Optional volume repair exists only for documented container data paths and drops privileges before serving; do not serve the web app as root. Use the host's real TLS/DNS controls and provider outbound access. Windows/local scripts were supplied but not executed on Windows here.

Run `python scripts/doctor.py --strict` inside the configured environment. Add `--square-required` for a deployment offering Square. Startup/doctor checks are necessary, not sufficient proof of a safe deployment.

## 5. Acceptance before public launch

Use synthetic users in staging to verify registration/email verification/login/reset; US ZIP/state/area checks; pending request with no charge; approval conflict; correct subject/From/Reply-To; in-person absence of checkout; Square OAuth, deposit, invoice balance, signed events/refunds; Paddle checkout/cancellation/retry; customer OTP/PDF signing/download; private exports; a full off-host restore; CSP/cookies/CSRF on actual HTTPS; and demo inability to reach real services. Test real device views and keyboard/accessibility behavior. Run a small authorized production smoke test only after provider/legal/operational gates pass. No live acceptance test was performed during this source review.
