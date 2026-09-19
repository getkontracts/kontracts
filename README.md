# Kontracts

A detailing workspace with approval-first bookings, business branding, customer records, Square or in-person payments, and retained signed invoices. FastAPI + SQLite + plain HTML/CSS/JavaScript. No frontend build or separate database server.

Production domain: **getkontracts.app**. The local app intentionally uses **http://localhost:8000**.

## Run locally

Use Python 3.13. On Windows, run `START-WINDOWS.bat`; subsequent launches use `run.bat`. On macOS/Linux:

```sh
sh setup.sh
```

Subsequent launches: `sh run.sh`. Open http://localhost:8000. Initial setup installs packages and the full US ZIP registry, so it needs internet. The small bundled registry is development-only and is rejected in production.

The included `.env` is configured for local development with no live credentials. `.env.example` is the **only** configuration template. No separate email patch or merge step is needed. Relative data/database paths resolve from the app folder, not the terminal's current folder.

## Email

| Address | Purpose | Replies |
| --- | --- | --- |
| accounts@getkontracts.app | Verification and password resets | Support |
| notifications@getkontracts.app | Bookings, quotes, reminders, invoices and signing codes | Detailer/customer as appropriate; signing codes use support |
| billing@getkontracts.app | Trial-ending and subscription-status notices | Support |
| support@getkontracts.app | Human support, outside the app | Your monitored inbox |

`MAIL_ENABLED=true` is set. **No provider key is included:** in local development, messages are saved as `.eml` files under `data/mail/`, not delivered to an inbox. Open those files to use local verification/reset links. The worker normally runs every 45 seconds.

For real delivery, verify getkontracts.app with the sending provider, set `RESEND_API_KEY` (or configure SMTP instead), and provision support receiving/forwarding. See [Email setup](docs/EMAIL_SETUP.md).

```sh
python scripts/email_admin.py check
```

## Preview

Open `Kontracts-Preview.html`, or `/demo` while the server is running. It uses synthetic temporary data and cannot send email, charge cards or create usable invoices. The simulated signing code is `123456`. Do not enter personal information. The preview is rebuilt from the current UI, not a separate app.

## Deploy or update an existing installation

Do not overwrite an existing `.env`, database or APP_SECRET. Stop the app, back up its data and matching secret, update the source, and merge the new mail settings from `.env.example`. Restarting applies additive schema migrations. Do not run the old email patch installer again.

For production, set `APP_ENV=production`, `BASE_URL=https://getkontracts.app`, a stable APP_SECRET and provider/policy settings. Keep one process with persistent storage. Update provider callback/webhook URLs to the new origin. Full steps: [Deployment](docs/DEPLOYMENT.md); backup/retention: [Operations](docs/OPERATIONS.md).

## Verify

```sh
python -m pip install -r requirements-dev.txt
python -m pip check
python scripts/verify_dependencies.py
python -m pytest -q
python scripts/doctor.py --strict
```

The strict doctor fails until production settings, full ZIP data, providers and policies are ready. [Test report](docs/TEST_REPORT.md) records what was actually verified; local passing tests are not live-delivery or production certification. See [Security review](docs/SECURITY_REVIEW.md) and [Legal checklist](docs/LEGAL_CHECKLIST.md) before public launch.
