# Operations, migration and records

## Upgrade without data loss

Stop the old application. Take a consistent database/photos backup and copy the existing APP_SECRET securely. Test a restore before upgrading. Keep existing database paths, provider IDs, legal texts and actual environment configuration; do not replace an existing production .env with the empty template. Never run two app versions against one SQLite file.

Startup performs additive schema migration. It preserves historical booking snapshots and marks previously confirmed/completed legacy bookings approved without fabricating electronic signatures. Older completed jobs have no signed PDF until an actual signing flow occurs; they are not silently certified. Unsubscribed accounts map to the one plan. Already-paid Paddle subscriptions and legacy domain entitlements are not automatically repriced/removed. Resolve old subscription mapping explicitly, with customer consent, before retiring historical price IDs.

The single new plan is internally named `solo` for compatibility. Old Google authentication/domain/price adapters remain for migration compatibility, but no new Google key or custom-domain connection is required by the standard UI or .env. Do not strip old credentials from an existing deployment until affected accounts are migrated safely.

## Backups and restoration

With the app stopped:

```sh
python scripts/backup.py /secure/path/backup-YYYYMMDD --app-is-stopped
```

The script snapshots SQLite via its backup API and copies photos. Treat the entire backup as personal data. Encrypt it, move a copy off-host, and store the matching APP_SECRET separately with restricted access. Include deployment configuration/provider recovery information and reviewed policies in your operator recovery procedure. Never put secrets into source control or browser files.

Restore into a separate staging directory first. Keep the app stopped; restore the database/photos and matching secret, enforce private permissions, start the matching release, run integrity/foreign-key checks, download a signed PDF and verify a sample booking. Only then replace production. Database-only copies without matching photos/secret are incomplete recovery plans. An in-process temporary-database backup test passed; the real hosting/storage restore still must be tested.

## Invoice retention and corrections

PDF version 0 is the detailer-signed issue snapshot. Version 1 adds the customer's email-verified acknowledgement; both remain available in account exports. Payment receipts and refund adjustments are separate so they do not rewrite signed service facts. Owner exports include canonical document data, PDF base64, hashes/seals and receipt records, but not customer bearer tokens. Export files contain sensitive data and must be encrypted/stored accordingly.

Customer access links expire after 30 days, independent of retention; the authenticated detailer can send a fresh link to the original invoice email. Old links are invalidated. Signing codes expire after 10 minutes and lock after five failed attempts; a fresh code can be requested subject to rate limits.

Normal API operations cannot edit/delete signed versions. There is no automated retention purge or credit-note accounting module. Establish lawful retention periods and an audited administrator-controlled correction/erasure/legal-hold procedure before launch. Do not promise lifetime/unlimited retention. Monitor database growth and free space; invoice PDFs are not part of the photo quota.

A disputed amount, service mistake or changed recipient email requires human handling. Do not alter evidence to pretend a different person signed. Discuss paper alternatives and corrected records with the customer and retain an audit trail.

## Payments and monitoring

Watch pending approvals, awaiting signatures, failed outbox attempts, payment-review flags, webhook failures, backups and disk usage. Pending requests expire after 48 hours or appointment start, whichever comes first; they do not reserve a slot. Square deposit holds expire separately. Approval email delivery is queued/retried, not guaranteed instantaneous.

Changes to Square/in-person preference affect future requests, not existing obligations. Keep the Square connection while retained Square invoices require verification/refunds. Disconnect is deliberately blocked while applicable invoice records exist; merchant offboarding needs an operator migration/review. Refund changes are recorded without rewriting PDFs and pause further collection. An external Square link already issued may still need to be cancelled in Square by the merchant. Verify refunds and chargebacks there; do not infer payment from a customer return page.

The bounded reconciliation worker covers recent 90-day invoice/deposit activity. Signed provider webhooks are still needed for older events. Manual, split, excess, tipped or unexpected provider payments must be investigated; they are not silently accepted as the expected balance.

Monitor support@getkontracts.app outside the app. Customer booking replies are addressed to the detailer; supported owner notifications reply to the customer. Account and platform subscription replies go to support. No hosted inbox or inbound automation is included. See EMAIL_SETUP.md for delivery checks and retries. Disable access logs that expose bearer URLs; configure proxy/application error logging with redaction and short retention. Never paste real invoice links into public support tickets.
