# Kontracts

One detailing workspace, one $49 USD monthly software plan. Approval based bookings, private customer conversations, service city and ZIP coverage, optimized photos, reviews and retained signed invoices. Square is for client service payments; Paddle is for the detailer's software subscription. They are separate integrations and separate navigation sections.

FastAPI, SQLite and plain HTML/CSS/JavaScript. No frontend build, additional database service, chat server or new runtime dependency was added in this update.

## Start with the demo

Open **START-HERE.html**, then **Kontracts-Preview.html**. The self contained demo uses synthetic temporary data. It cannot send email, charge a card or create a real subscription. The simulated invoice signing code is `123456`. “Leave demo and subscribe” exits to the real account creation page. Do not enter personal information in the demo.

The deployed demo is also available at `/demo`. It is built from the same UI as the real application.

## Run the application locally

Use Python 3.13. On Windows run `START-WINDOWS.bat`; subsequent launches use `run.bat`. On macOS/Linux:

```sh
sh setup.sh
```

Subsequent launches: `sh run.sh`. Open `http://localhost:8000`.

Initial installation needs internet for the pinned Python packages and the full US city and ZIP registry. This archive includes only a clearly marked development geography sample; production refuses it. To install or update nationwide data:

```sh
python scripts/update_zipcodes.py
# Or download GeoNames US.zip on a networked machine, then:
python scripts/update_zipcodes.py --input /path/to/US.zip
```

Restart after a registry update. City searches use the local server registry, not a third party request for every booking. The scope is the 50 states and Washington, DC. Postal cities and ZIP areas are not exact municipal boundaries or street address verification.

Only **.env.example** is included. The startup scripts copy it to `.env` for a new installation. Keep the existing `.env`, `APP_SECRET` and data on an upgrade. Never share real provider secrets.

## Your detailer workflow

In **Settings**, save your business details, select each service city, and choose citywide postal coverage or specific ZIP codes. In **Prices**, set the time to detail one car, vehicle adjustments, extras and prices. Review availability and the travel buffer. Review the policy before publishing.

A client's request appears in **Bookings** awaiting approval. Open it to ask for details in a private conversation, approve it, add before or after photos, or save payment proof. Quote requests have their own private conversations. **Customers** brings together contact details, bookings, quotes, private notes, reviews and messages. **Messages** is the conversation inbox.

Complete the invoice signing workflow after the job, then use **Request a review** on the completed booking. Reviews are private feedback shared with the detailer; this does not post to Google or another public review service.

Clients may add optional condition photos when booking, and condition or payment proof photos from their private booking link. A payment proof image never marks a job paid. Verify a direct payment in your own account, then use **Invoices → Record payment** and select Cash, Venmo, Cash App or Other direct payment. Square receipts use separate provider verification.

## Square and Paddle

**Customer payments** contains Square authorization, location selection, deposit rules and direct payment preferences. **Subscription** contains Paddle checkout, provider verified membership status and the subscription portal. Existing contracted subscriptions are not silently repriced. A provider confirmed trial subscription is labeled subscribed during its trial; an unpurchased app trial remains a free trial.

To add the separate marketing button, set the actual affiliate URL in the deployment's `.env`:

```dotenv
SQUARE_REFERRAL_URL=https://your-actual-square-referral-url
```

Replace the example with your actual HTTPS referral address. No referral URL was supplied with this task; the field is blank by default. It does not replace an OAuth callback, webhook, checkout, deposit or payment URL. The UI identifies it as a referral link.

## Email

Local development without a mail provider writes `.eml` files to `data/mail/` rather than delivering email. Real delivery requires verified sending identities and Resend or SMTP configuration. The worker normally runs every 45 seconds. Conversation and review notifications are queued; a successful queue operation is not proof of inbox delivery.

Accounts mail uses `accounts@getkontracts.app`, job notifications use `notifications@getkontracts.app`, billing notices use `billing@getkontracts.app`, and human support uses `support@getkontracts.app`. Chat replies must be made inside the app; replying to a notification email does not add a conversation message. See [Email setup](docs/EMAIL_SETUP.md).

## Upgrade and launch

Read [Release notes](docs/RELEASE_NOTES.md) and [Launch checklist](docs/LAUNCH_CHECKLIST.md) before replacing an installation. Stop the app and back up the database, files and matching secret. Apply the source update without overwriting `.env` or data, install dependencies, update geography and restart.

**Existing published pages without reviewed city selections are unpublished once by migration 8.** Existing bookings, private links, invoices, receipts and photos remain. Each affected detailer must review cities and coverage in Settings and republish. The old ZIP list is kept; the migration never silently expands a service area.

The intended deployment is one process and one replica with persistent private storage, HTTPS, configured providers and reviewed legal policies. Details: [Deployment](docs/DEPLOYMENT.md), [Operations](docs/OPERATIONS.md), [Security scope](docs/SECURITY_REVIEW.md).

## Reproduce verification

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

Node is useful for `node --check web/assets/app.js` and the other JavaScript files, but is not a runtime dependency. Run `python scripts/doctor.py --strict` on the configured deployment, adding `--square-required` when offering Square.

[Test report](docs/TEST_REPORT.md) records local results and their limits. Local passing tests do not establish live payment delivery, a successful production dependency installation, legal compliance, physical device compatibility or absence of all defects.
