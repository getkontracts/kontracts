# Customer experience update | 1 October 2026

## Payment provider boundaries

Square implementation now lives in `server/square_gateway.py` and `server/square_connections.py`. Paddle subscription implementation lives in `server/paddle_gateway.py` and `server/billing.py`. `server/payments.py` is a compatibility facade with neutral HTTP transport and separate lock keys. Credentials, provider event handling, payment records and money flows are not combined.

Square OAuth callback and webhook paths, original authorization scopes, deposit idempotency and verification remain in place. Multi location authorization now asks the detailer to select an eligible US USD location instead of silently taking the first location. Pending tokens are encrypted and the selection expires after ten minutes. The previous connection is preserved on an unsuccessful selection. An account or location change that could orphan historical Square payment records is rejected with guidance.

The new HTTPS `SQUARE_REFERRAL_URL` is marketing configuration only. It produces a separate Get Square button with referral disclosure and safe new window attributes. Blank configuration does not invent a referral link. Referral configuration is not consulted by OAuth, deposits, payment creation or webhooks.

Paddle verification can reconcile an existing checkout/subscription after checkout return. It cannot create a second checkout or manufacture a paid status. Customer/subscription binding, configured price recognition, status ordering and idempotency are checked using the same subscription application rules as provider events. Verified provider trial memberships show “Subscribed (trial period).” A free app trial without a provider subscription does not.

## Geography and duration

Detailers select US postal cities from a searchable server registry and choose all listed ZIP codes or selected ZIP codes in each city. Selected ZIPs must belong to that city. Saved coverage is rechecked on pricing, quote submission and booking submission; strings preserve leading zeroes. City and state search results are bounded and debounced. A stale price response cannot overwrite a more recent ZIP selection.

The full GeoNames importer validates nationwide coverage, count, size, source checksum and license metadata, then atomically updates the local registry. This environment could not download nationwide data. The included registry remains a development sample, and production rejects it. Install the full registry before launch and repeat ZIP tests against that dataset. Citywide is postal coverage, not a municipal boundary guarantee.

Service editing labels the per car duration explicitly. Service time, individual vehicle duration, extras and travel buffer continue to drive real appointment availability, not just display text.

## Customers, conversations and reviews

Customer cards offer searchable contact details, bookings, quote only leads, combined history, private business notes, reviews and message counts. Completed service value is labeled separately from verified payment totals. Private note revisions reject stale concurrent edits.

Pending bookings and quote requests have Ask for more details actions. Each conversation is scoped to its booking or quote and business. Owner roles are derived from authenticated ownership; customers use an opaque private request link. Text is escaped in the UI, sends are idempotent, messages have pagination/read markers, and the open conversation refreshes every fifteen seconds. Notifications are queued by email and contain a link back to the app. No WebSocket service or inbound email parser is introduced.

Customer conversations remain separate from booking approval and payment. Access also respects the business boundary on custom domains. Private message and note records are included in the authenticated account export.

The detailer can request a review for a completed job. Every rating from one to five is accepted. Requests and submissions are deduplicated, and feedback appears in customer history. These are private reviews, not public platform reviews or reputation filtering.

## Photos and direct receipts

Normal booking requests now accept optional photos, not only exceptional quote requests. A saved booking is retained when an upload fails; retrying the same request cannot create another booking or duplicate a confirmed upload. Customers can add condition photos while a job is open, and payment proof after completion. Customers cannot assign detailer before/after categories.

Before photo, After photo and Payment proof actions are visible on job details. Mobile capture hints, client and server compression, metadata removal, private access and existing quotas are retained. Customer upload authorization is checked again inside the transaction after image processing, preventing a concurrent cancellation from accepting a new upload.

Invoice recording uses an in-app dialog with a method, optional reference and explicit confirmation of the full remaining balance. A proof image alone changes no payment state. Square invoices still require Square verification. Direct receipts and optional references are immutable; signed invoice bytes are not rewritten by this UI change. Partial direct receipts were not added.

## Copy, demo and mobile

The public offer and fallback terms now refer to one Kontracts plan at USD49 monthly, plus applicable tax. Read only compatibility identifiers for existing provider contracts remain internal; they are not additional plans for sale. Operator supplied production policy files must be updated and reviewed separately.

Display copy uses ordinary spaced words instead of hyphenated wording where cleaned. Technical identifiers, routes, URLs, filenames and user supplied content are intentionally not rewritten. Error messages explain rejected coverage, incomplete setup, provider problems and required next steps rather than leaking raw provider errors.

The demo contains the new in-memory workflows and an explicit route to the real signup page. It still cannot take payments, deliver mail or produce usable invoice records. Navigation to the real site requires the explicit exit link. No real account form is shown inside the simulated signup route.

Forms and dialogs have mobile sizing and touch targets. Foreground conversation polling stops when its modal is closed or the tab is hidden. Images are optimized; no frontend framework or new runtime package is required.

## Upgrade safety

Migrations 7 and 8 add conversations, review requests, private notes, quote submission idempotency, receipt references and encrypted pending Square selections. The legacy photo category check is upgraded without dropping retained images. Migration 8 unpublishes only already published businesses lacking reviewed city areas. Detailers must save their cities and republish; existing jobs and private links survive.

Back up and restore in staging before production. Keep the same APP_SECRET, data and provider configuration. This source update does not deploy itself, reprice existing provider contracts, rewrite signed invoices, delete customer records, or supply live payment/mail credentials.
