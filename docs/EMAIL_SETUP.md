# Email setup

## One configuration, four identities

Use only `.env` (template: `.env.example`). The email patch is already integrated. There is no patch installer, extra email configuration file or separate mail service to launch.

| Identity | Outgoing use | Reply-To |
| --- | --- | --- |
| accounts@getkontracts.app | Account verification and password reset | support@getkontracts.app |
| notifications@getkontracts.app | Quotes, bookings, reminders, invoices and service payment updates | Detailer's contact email for customer messages; customer's email for supported owner notices |
| billing@getkontracts.app | Kontracts trial and subscription status | support@getkontracts.app |
| support@getkontracts.app | Human support in your mail client | Your receiving inbox |

Invoice signing codes are sent from notifications with Reply-To set to support. Detailers can change their business display name and contact address, not the authenticated sending domain. A missing/invalid detailer contact falls back to support. Paddle's official receipts remain separate from the app's subscription-status notices.

## Local mode versus real delivery

`MAIL_ENABLED=true` enables the outbox worker. Without Resend/SMTP credentials, development writes `.eml` files under `data/mail/`; **nothing reaches an external inbox**. The worker runs approximately every 45 seconds. Open the saved email to follow a local account link. With `MAIL_ENABLED=false`, mail stays queued. Production now refuses that disabled setting because account verification and invoice signing depend on email.

Check the effective settings without sending anything:

```sh
python scripts/email_admin.py check
python scripts/email_admin.py status
```

The first command works before the database exists; status requires an initialized app database. Neither prints credentials. The setup API distinguishes disabled, local-eml and provider modes.

## Enable live delivery

In the production environment, set `BASE_URL=https://getkontracts.app` so account, booking, invoice and callback links use the new site. Set `MAIL_ENABLED=true` and retain the sender/reply identities from `.env.example`.

Verify **getkontracts.app** in Resend. Use the exact DNS records shown in its dashboard, not example DKIM keys or guessed MX values. Set an application sending key in `RESEND_API_KEY`; leave SMTP blank. A verified sender domain permits the app's three sending identities. Alternatively use your authenticated TLS SMTP relay with `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` and `SMTP_PASSWORD`, leaving the Resend key blank. Ports 587/STARTTLS and 465/implicit TLS are supported.

Provision a receiving mailbox or forwarding for **support@getkontracts.app**. Route the other three identities to that monitored inbox too when direct mail to them should be received. Keep the existing receiving provider's root MX records; sending verification does not itself provision a monitored human inbox. Configure authenticated outgoing support replies in your mail client. The app has no human-support composer or inbound mailbox integration.

After DNS verification, test forwarding and support replies from an independent inbox. Never put provider secrets into JavaScript, a demo, screenshots or a public repository.

Official references: https://resend.com/docs/dashboard/domains/introduction and https://resend.com/docs/api-reference/emails/send-email .

## Confirm all three app senders

Stop the app before using a mutating administration command. A dry run sends nothing:

```sh
python scripts/email_admin.py smoke --to YOUR_TEST_INBOX
```

After configuring the provider, deliberately send three synthetic probes:

```sh
python scripts/email_admin.py smoke --to YOUR_TEST_INBOX --send --app-is-stopped
```

This queues and attempts only those three probes, not the entire pending queue. Add `--shop-slug YOUR_BUSINESS_SLUG` for real business branding. Check From and Reply-To, SPF/DKIM/DMARC results and inbox placement. Provider acceptance is not proof of inbox delivery. Restart the app afterward. Verify the fourth identity by sending a human support reply from the receiving mailbox.

## Existing data, retries and limits

Keep the original APP_SECRET and database when updating. Never apply global search/replace to customer addresses or signed invoice data. Previously issued links are not retroactively changed; use the app's fresh-link/reset actions after changing BASE_URL. Old links require a deliberately configured old-domain redirect if that domain remains under your control. Provider webhooks/OAuth callbacks must also be updated in provider dashboards.

Pending messages with a frozen delivery payload retain their original sender/body for safe retries. Changing `.env` does not rewrite those messages. Inspect any old-domain queue entries before restarting delivery; cancel obsolete messages and create fresh ones through the relevant app action. Do not blindly replay old signing codes, expired links or potentially accepted messages.

```sh
python scripts/email_admin.py cancel MESSAGE_ID --app-is-stopped
```

For other pending messages, only after confirming with the provider that the message was not accepted:

```sh
python scripts/email_admin.py retry MESSAGE_ID --confirmed-not-accepted --app-is-stopped
```

This command only requeues; it does not send immediately or rewrite the old payload. It refuses account links/signing codes: request fresh ones in the app. The normal worker preserves payloads/idempotency keys, pauses on quota responses and suppresses expired or superseded account links, codes and reminders. Automatic retries stop before the provider's idempotency window ends. Official reference: https://resend.com/docs/dashboard/emails/idempotency-keys .

The defaults cap accepted application sends at 90 per UTC day and 2,700 per rolling 31 days. These are app safety caps, not promises about a provider plan, and they do not count your mail client or other applications. Check provider usage separately. Sending acceptance, inbound forwarding and successful customer replies were not tested against live accounts in this source repair.
