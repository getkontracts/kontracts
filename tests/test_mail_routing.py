"""Email routing regression tests. All outbound transports are mocked."""
import copy
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr

import httpx
import pytest

from server import billing, branding, config, core, mail, payments, worker
from server.db import SCHEMA, db, initialize, now, packed, queue_mail, uid
from tests.test_app import shop, booking
from tests.test_workflow import issue, signing_code, sign


@pytest.fixture(autouse=True)
def identities(monkeypatch):
    for key, value in {
        'MAIL_ACCOUNTS_FROM': 'Kontracts <accounts@getkontracts.app>',
        'MAIL_ACCOUNTS_ADDRESS': 'accounts@getkontracts.app',
        'MAIL_NOTIFICATIONS_FROM': 'Kontracts <notifications@getkontracts.app>',
        'MAIL_NOTIFICATIONS_ADDRESS': 'notifications@getkontracts.app',
        'MAIL_BILLING_FROM': 'Kontracts Billing <billing@getkontracts.app>',
        'MAIL_BILLING_ADDRESS': 'billing@getkontracts.app',
        'MAIL_SENDER_DOMAIN': 'getkontracts.app',
        'SUPPORT_EMAIL': 'support@getkontracts.app', 'MAIL_REPLY_TO': 'support@getkontracts.app',
        'MAIL_ENABLED': True, 'MAIL_BILLING_NOTICES': True, 'MAIL_SEND_INTERVAL': 0,
        'MAIL_DAILY_LIMIT': 90, 'MAIL_31DAY_LIMIT': 2700,
        'RESEND_API_KEY': '', 'SMTP_HOST': '',
    }.items():
        monkeypatch.setattr(config, key, value)


def queue_one(key='email-test', **kwargs):
    with db(True) as c:
        queue_mail(c, key, 'customer@example.com', 'Test subject', 'Test body', **kwargs)
        return dict(c.execute('SELECT * FROM outbox WHERE dedupe_key=?', (key,)).fetchone())


def row(identifier):
    with db() as c:
        return dict(c.execute('SELECT * FROM outbox WHERE id=?', (identifier,)).fetchone())


def fake_resend(monkeypatch, status=200, response=None, headers=None):
    seen = []
    monkeypatch.setattr(config, 'RESEND_API_KEY', 'test-key-not-live')
    def post(url, **kwargs):
        seen.append(copy.deepcopy(kwargs))
        return httpx.Response(status, json=response if response is not None else {'id': 'provider-' + str(len(seen))},
                              headers=headers, request=httpx.Request('POST', url))
    monkeypatch.setattr(worker.httpx, 'post', post)
    return seen


def test_accounts_channel_uses_platform_sender_and_support(client, monkeypatch):
    m = queue_one()
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    assert seen[0]['json']['from'] == config.MAIL_ACCOUNTS_FROM
    assert seen[0]['json']['reply_to'] == 'support@getkontracts.app'
    assert 'html' not in seen[0]['json']
    assert row(m['id'])['provider_id'] == 'provider-1'


def test_customer_notification_is_branded_and_replies_to_detailer(owner, monkeypatch):
    m = queue_one(shop_id=shop()['id'])
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    payload = seen[0]['json']
    assert parseaddr(payload['from']) == (shop()['name'], 'notifications@getkontracts.app')
    assert payload['reply_to'] == core.settings(shop())['contact_email']
    assert payload['reply_to'] in payload['html']
    assert 'mailto:support@getkontracts.app' not in payload['html']
    assert m['mail_channel'] == 'notifications'


def test_billing_with_shop_is_not_detailer_branded(owner, monkeypatch):
    queue_one(shop_id=shop()['id'], channel='billing')
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    assert seen[0]['json']['from'] == config.MAIL_BILLING_FROM
    assert seen[0]['json']['subject'] == 'Test subject'
    assert seen[0]['json']['reply_to'] == config.SUPPORT_EMAIL
    assert 'html' not in seen[0]['json']


def test_booking_replies_flow_in_both_directions(owner):
    response = owner.post('/api/public/shops/northline/book', json=booking(email='client-customer@example.com'))
    assert response.status_code == 201
    identifier = response.json()['id']
    with db() as c:
        rows = c.execute('SELECT * FROM outbox WHERE dedupe_key LIKE ?', (identifier + ':%',)).fetchall()
    customer = next(m for m in rows if m['dedupe_key'].endswith(':customer'))
    detailer = next(m for m in rows if m['dedupe_key'].endswith(':owner'))
    assert customer['reply_to'] == core.settings(shop())['contact_email']
    assert detailer['reply_to'] == 'client-customer@example.com'
    assert customer['mail_channel'] == detailer['mail_channel'] == 'notifications'
    assert core.settings(shop())['contact_email'] in customer['body']


def test_public_correspondence_is_detailer_not_platform(owner):
    response = owner.get('/api/public/shops/northline').json()
    assert response['correspondence_email'] == core.settings(shop())['contact_email']


def test_invalid_legacy_detailer_contact_falls_back_to_support(owner):
    s = dict(shop())
    settings = json.loads(s['settings'])
    settings['contact_email'] = 'not-valid\r\nBcc: thief@example.com'
    s['settings'] = packed(settings)
    assert mail.shop_contact(s) == config.SUPPORT_EMAIL


@pytest.mark.parametrize('bad', ['a@example.com,b@example.com', 'Name <a@example.com>',
                                'a@example.com\r\nBcc: b@example.com', 'not-an-email', '', None])
def test_recipient_and_reply_validation_rejects_bad_input(client, bad):
    with db(True) as c:
        with pytest.raises(ValueError):
            queue_mail(c, uid(), bad, 'Test', 'body')
        with pytest.raises(ValueError):
            # None means "use default" and is deliberately valid as reply_to.
            if bad is None:
                mail.mailbox(bad, address_only=True)
            else:
                queue_mail(c, uid(), 'good@example.com', 'Test', 'body', reply_to=bad)


@pytest.mark.parametrize('bad', ['evil', 'support', 'marketing'])
def test_unknown_channel_rejected(client, bad):
    with pytest.raises(ValueError):
        queue_one(channel=bad)


def test_safe_name_cannot_inject_headers():
    name = mail.clean_name('Business\r\nBcc: other@example.com\x00')
    assert '\r' not in name and '\n' not in name and '\x00' not in name


def test_same_event_cannot_queue_twice(client):
    first = queue_one()
    second = queue_one()
    assert first['id'] == second['id']
    with db() as c:
        assert c.execute('SELECT count(*) FROM outbox').fetchone()[0] == 1


def test_demo_mail_never_queues(owner):
    with db(True) as c:
        c.execute('UPDATE shops SET is_demo=1 WHERE id=?', (shop()['id'],))
        queue_mail(c, 'demo-notice', 'customer@example.com', 'No email', 'No send', shop()['id'], channel='billing')
        assert c.execute("SELECT count(*) FROM outbox WHERE dedupe_key='demo-notice'").fetchone()[0] == 0


def test_disabled_mail_does_not_send_or_consume_attempt(client, monkeypatch):
    m = queue_one()
    monkeypatch.setattr(config, 'MAIL_ENABLED', False)
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    assert seen == [] and row(m['id'])['attempts'] == 0


def test_expired_message_is_cancelled_not_marked_sent(client, monkeypatch):
    m = queue_one(expires_at=now() - 1)
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    r = row(m['id'])
    assert seen == [] and r['cancelled_at'] and r['sent_at'] is None and r['attempts'] == 0


def test_replaced_auth_link_is_not_sent(owner, monkeypatch):
    from server.app import auth_mail
    with db(True) as c:
        c.execute('DELETE FROM outbox')
        s = shop()
        auth_mail(c, s['owner_id'], 'owner@example.com', 'reset')
        auth_mail(c, s['owner_id'], 'owner@example.com', 'reset')
    seen = fake_resend(monkeypatch)
    worker.deliver(5)
    assert len(seen) == 1
    with db() as c:
        states = c.execute('SELECT sent_at,cancelled_at FROM outbox').fetchall()
    assert sum(bool(r['sent_at']) for r in states) == 1
    assert sum(bool(r['cancelled_at']) for r in states) == 1


def test_signing_code_replies_to_support_and_replaced_code_does_not_send(owner, monkeypatch):
    inv, raw = issue(owner)
    with db(True) as c:
        c.execute('DELETE FROM outbox')
    codes = iter([111111, 222222])
    from server import invoices
    monkeypatch.setattr(invoices.secrets, 'randbelow', lambda maximum: next(codes))
    signing_code(owner, raw)
    signing_code(owner, raw)
    seen = fake_resend(monkeypatch)
    worker.deliver(5)
    assert len(seen) == 1
    assert '222222' in seen[0]['json']['text'] and '111111' not in seen[0]['json']['text']
    assert parseaddr(seen[0]['json']['from'])[1] == 'notifications@getkontracts.app'
    assert seen[0]['json']['reply_to'] == config.SUPPORT_EMAIL
    with db() as c:
        assert c.execute('SELECT count(*) FROM outbox WHERE cancelled_at IS NOT NULL').fetchone()[0] == 1


def test_rotated_invoice_link_is_not_sent(owner, monkeypatch):
    inv, raw = issue(owner)
    with db(True) as c:
        c.execute("DELETE FROM outbox WHERE guard_kind IS NULL OR guard_kind!='invoice_link'")
    assert owner.post('/api/invoices/' + inv['id'] + '/send-link').status_code == 200
    seen = fake_resend(monkeypatch)
    worker.deliver(5)
    assert len(seen) == 1 and raw not in seen[0]['json']['text']


def test_invoice_correspondence_keeps_sealed_snapshot(owner):
    inv, raw = issue(owner)
    before = owner.get('/api/public/invoices/' + raw).json()
    original_contact = before['document']['business']['correspondence_email']
    assert original_contact == core.settings(shop())['contact_email']
    with db(True) as c:
        s = shop(); settings = core.settings(s); settings['contact_email'] = 'new-address@example.com'
        c.execute('UPDATE shops SET settings=? WHERE id=?', (packed(settings), s['id']))
    after = owner.get('/api/public/invoices/' + raw).json()
    assert after['correspondence_email'] == original_contact
    assert after['pdf_sha256'] == before['pdf_sha256']


def test_signed_invoice_owner_reply_goes_to_original_customer(owner):
    inv, raw = issue(owner)
    assert sign(owner, raw).status_code == 200
    with db() as c:
        message = c.execute('SELECT * FROM outbox WHERE dedupe_key=?', (inv['id'] + ':owner-signed',)).fetchone()
        from server.invoices import read_version
        customer = read_version(c, inv['id'], 0)[1]['customer']['email']
    assert message['reply_to'] == customer


def test_resend_payload_is_frozen_across_branding_changes(owner, monkeypatch):
    m = queue_one(shop_id=shop()['id'])
    seen = fake_resend(monkeypatch, 503, {'name': 'service_unavailable'})
    worker.deliver(1)
    first = seen[0]
    with db(True) as c:
        s = shop(); settings = core.settings(s); settings['contact_email'] = 'changed@example.com'
        c.execute('UPDATE shops SET name=?,settings=? WHERE id=?', ('Changed name', packed(settings), s['id']))
        c.execute('UPDATE outbox SET due_at=? WHERE id=?', (now(), m['id']))
    second = fake_resend(monkeypatch)
    worker.deliver(1)
    assert second[0]['json'] == first['json']
    assert second[0]['headers']['Idempotency-Key'] == first['headers']['Idempotency-Key']
    assert row(m['id'])['sent_at'] is not None


@pytest.mark.parametrize('name', ['daily_quota_exceeded', 'monthly_quota_exceeded', 'rate_limit_exceeded'])
def test_resend_quota_pauses_without_exhausting_retry_budget(client, monkeypatch, name):
    m = queue_one()
    queue_one('next')
    seen = fake_resend(monkeypatch, 429, {'name': name}, {'Retry-After': '120'})
    worker.deliver(20)
    worker.deliver(20)
    assert len(seen) == 1
    r = row(m['id'])
    assert r['attempts'] == 0 and r['first_attempt_at'] is None
    assert r['sent_at'] is None and r['due_at'] >= now() + 110
    assert name in r['last_error']


def test_retry_after_http_date():
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    response = httpx.Response(429, headers={'Retry-After': later.strftime('%a, %d %b %Y %H:%M:%S GMT')})
    assert worker._retry_after(response, now()) >= now() + 290


@pytest.mark.parametrize('value', ['NaN', 'Infinity', 'garbage', ''])
def test_malformed_retry_after_is_safe(value):
    response = httpx.Response(429, headers={'Retry-After': value})
    assert worker._retry_after(response, 1000) == 1060


def test_local_daily_limit_does_not_attempt_next_message(client, monkeypatch):
    monkeypatch.setattr(config, 'MAIL_DAILY_LIMIT', 1)
    first = queue_one('one'); second = queue_one('two')
    seen = fake_resend(monkeypatch)
    worker.deliver(10)
    assert len(seen) == 1
    assert row(first['id'])['sent_at'] and row(second['id'])['attempts'] == 0
    assert 'daily allowance' in row(second['id'])['last_error']


def test_rolling_quota_has_persistent_ledger_after_tenant_deletion(owner, monkeypatch):
    monkeypatch.setattr(config, 'MAIL_31DAY_LIMIT', 1)
    m = queue_one(shop_id=shop()['id'])
    fake_resend(monkeypatch)
    worker.deliver(1)
    with db(True) as c:
        c.execute('DELETE FROM outbox WHERE id=?', (m['id'],))
        state = worker.local_quota_state(c)
    assert state['rolling_31day_used'] == 1 and state['blocked_until'] > now()


def test_local_eml_does_not_consume_provider_allowance(client):
    m = queue_one()
    worker.deliver(1)
    assert (config.DATA / 'mail' / (m['id'] + '.eml')).is_file()
    with db() as c:
        assert worker.local_quota_state(c)['daily_used'] == 0


def test_old_ambiguous_send_requires_manual_review(client, monkeypatch):
    m = queue_one()
    with db(True) as c:
        c.execute('UPDATE outbox SET first_attempt_at=?,attempts=1 WHERE id=?', (now() - 24 * 3600, m['id']))
    seen = fake_resend(monkeypatch)
    worker.deliver(1)
    assert seen == []
    assert row(m['id'])['attempts'] == 8
    assert row(m['id'])['last_error'].startswith('ManualReview:')


def test_bad_presentation_is_caught_and_does_not_abandon_claim(client, monkeypatch):
    m = queue_one()
    def broken(message):
        raise RuntimeError('render failed')
    monkeypatch.setattr(branding, 'email_presentation', broken)
    worker.deliver(1)
    r = row(m['id'])
    assert r['claimed_at'] is None and r['attempts'] == 1 and r['last_error'] == 'RuntimeError'


@pytest.mark.parametrize('port', [465, 587])
def test_smtp_uses_channel_sender_reply_and_tls(client, monkeypatch, port):
    m = queue_one(channel='billing')
    monkeypatch.setattr(config, 'SMTP_HOST', 'smtp.resend.com')
    monkeypatch.setattr(config, 'SMTP_PORT', port)
    monkeypatch.setattr(config, 'SMTP_USER', 'resend')
    monkeypatch.setattr(config, 'SMTP_PASSWORD', 'fake-support-key')
    calls = []
    class SMTP:
        def __init__(self, *args, **kwargs): calls.append(('connect', args))
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self, **kwargs): calls.append(('starttls',))
        def login(self, *args): calls.append(('login', args))
        def send_message(self, msg): calls.append(('send', msg)); return {}
    monkeypatch.setattr(worker.smtplib, 'SMTP_SSL' if port == 465 else 'SMTP', SMTP)
    worker.deliver(1)
    msg = next(c[1] for c in calls if c[0] == 'send')
    assert str(msg['From']) == config.MAIL_BILLING_FROM
    assert str(msg['Reply-To']) == config.SUPPORT_EMAIL
    assert str(msg['Resend-Idempotency-Key']) == 'kontracts-mail/' + m['id']
    assert any(c[0] == 'starttls' for c in calls) == (port == 587)
    assert row(m['id'])['sent_at'] is not None


def paddle_event(s, identifier, status, when):
    return {'event_id': identifier, 'event_type': 'subscription.updated', 'occurred_at': when,
            'data': {'id': 'sub_mail', 'customer_id': 'ctm_mail', 'status': status,
                     'custom_data': {'shop_id': s['id'], 'binding': billing.binding(s['id'], 'ctm_mail')},
                     'items': [{'price': {'id': 'pri_mail'}, 'quantity': 1}]}}


def test_billing_notice_follows_verified_state_change_not_webhook_replay(owner, monkeypatch):
    monkeypatch.setattr(config, 'PADDLE_PRICE', 'pri_mail')
    s = shop()
    with db(True) as c:
        c.execute("UPDATE shops SET paddle_customer='ctm_mail',billing_status='trial' WHERE id=?", (s['id'],))
    event = paddle_event(s, 'evt_first', 'active', '2026-09-18T00:00:00Z')
    payments.apply_paddle(event)
    payments.apply_paddle(event)
    payments.apply_paddle(paddle_event(s, 'evt_older', 'past_due', '2026-09-17T00:00:00Z'))
    payments.apply_paddle(paddle_event(s, 'evt_same', 'active', '2026-09-18T01:00:00Z'))
    with db() as c:
        rows = c.execute("SELECT * FROM outbox WHERE mail_channel='billing'").fetchall()
        owner_email = c.execute('SELECT email FROM users WHERE id=?', (s['owner_id'],)).fetchone()[0]
    assert len(rows) == 1 and rows[0]['recipient'] == owner_email
    assert rows[0]['subject'] == 'Your Kontracts subscription is active'
    assert 'not a payment receipt' in rows[0]['body']


def test_trial_notice_is_single_and_cancelled_after_subscription(owner, monkeypatch):
    with db(True) as c:
        c.execute("UPDATE shops SET billing_status='trial',trial_end=?,paddle_subscription=NULL WHERE id=?", (now() + 3600, shop()['id']))
        mail.queue_trial_notices(c)
        mail.queue_trial_notices(c)
        c.execute("UPDATE shops SET billing_status='active' WHERE id=?", (shop()['id'],))
        assert c.execute("SELECT count(*) FROM outbox WHERE mail_channel='billing'").fetchone()[0] == 1
    seen = fake_resend(monkeypatch)
    worker.deliver(10)
    assert seen == []
    with db() as c:
        assert c.execute("SELECT cancelled_at FROM outbox WHERE mail_channel='billing'").fetchone()[0]


def test_billing_notices_can_be_disabled(owner, monkeypatch):
    monkeypatch.setattr(config, 'MAIL_BILLING_NOTICES', False)
    with db(True) as c:
        mail.queue_subscription_notice(c, shop(), 'canceled', 'disabled-event')
        mail.queue_trial_notices(c)
        assert c.execute("SELECT count(*) FROM outbox WHERE mail_channel='billing'").fetchone()[0] == 0


def test_migration_preserves_legacy_rows_and_parks_attempted_payload(owner):
    from server.email_schema import initialize as migrate
    with db() as c:
        c.execute('DROP TABLE outbox')
        c.execute('DELETE FROM schema_versions WHERE version=6')
        c.executescript(SCHEMA)
        for key, attempts, sent_at in [('pending', 0, None), ('uncertain', 1, None), ('accepted', 1, now())]:
            c.execute('''INSERT INTO outbox(id,dedupe_key,shop_id,recipient,subject,body,due_at,attempts,sent_at)
                VALUES(?,?,?,?,?,?,?,?,?)''', (key, key, shop()['id'], 'customer@example.com', 'Legacy subject', 'Legacy body', now(), attempts, sent_at))
        migrate(c)
        migrate(c)
        assert c.execute('SELECT count(*) FROM outbox').fetchone()[0] == 3
        pending = c.execute("SELECT * FROM outbox WHERE id='pending'").fetchone()
        uncertain = c.execute("SELECT * FROM outbox WHERE id='uncertain'").fetchone()
        assert pending['mail_channel'] == 'notifications' and pending['attempts'] == 0
        assert pending['reply_to'] == core.settings(shop())['contact_email']
        assert uncertain['attempts'] == 8 and uncertain['last_error'].startswith('ManualReview:')
        assert c.execute('SELECT count(*) FROM mail_usage').fetchone()[0] == 1


def test_tenant_retry_cannot_replay_manual_review_message(owner):
    m = queue_one(shop_id=shop()['id'])
    with db(True) as c:
        c.execute("UPDATE outbox SET attempts=8,last_error='ManualReview: verify first' WHERE id=?", (m['id'],))
    response = owner.post('/api/operations/retry-emails')
    assert response.status_code == 200, response.text
    assert row(m['id'])['attempts'] == 8


def test_smoke_only_sends_selected_ids(client, monkeypatch):
    unrelated = queue_one('unrelated')
    selected = queue_one('selected')
    seen = fake_resend(monkeypatch)
    worker.deliver(10, message_ids=[selected['id']])
    assert len(seen) == 1
    assert row(unrelated['id'])['sent_at'] is None
    assert row(selected['id'])['sent_at'] is not None


def test_idn_domain_is_encoded_for_smtp():
    assert mail.mailbox('hello@b\u00fccher.de', address_only=True)[1] == 'hello@xn--bcher-kva.de'


def test_outdated_subscription_status_is_not_sent(owner, monkeypatch):
    monkeypatch.setattr(config, 'PADDLE_PRICE', 'pri_mail')
    s = shop()
    with db(True) as c:
        c.execute("UPDATE shops SET paddle_customer='ctm_mail',billing_status='trial' WHERE id=?", (s['id'],))
    payments.apply_paddle(paddle_event(s, 'evt_active', 'active', '2026-09-18T00:00:00Z'))
    payments.apply_paddle(paddle_event(s, 'evt_late', 'past_due', '2026-09-18T01:00:00Z'))
    seen = fake_resend(monkeypatch)
    worker.deliver(5)
    assert len(seen) == 1
    assert 'Action needed' in seen[0]['json']['subject']


def test_email_admin_check_hides_secret(client, monkeypatch, capsys):
    from scripts.email_admin import main
    monkeypatch.setattr(config, 'RESEND_API_KEY', 'SUPER-PRIVATE-KEY')
    assert main(['check']) == 0
    output = capsys.readouterr().out
    assert 'SUPER-PRIVATE-KEY' not in output and 'resend-api' in output


def test_email_admin_smoke_dry_run_does_not_queue(client, capsys):
    from scripts.email_admin import main
    assert main(['smoke', '--to', 'tester@example.com']) == 0
    assert json.loads(capsys.readouterr().out)['dry_run'] is True
    with db() as c:
        assert c.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0


def test_email_admin_retry_requires_acceptance_review(client):
    from scripts.email_admin import retry
    m = queue_one()
    with pytest.raises(RuntimeError, match='Check Resend first'):
        retry(m['id'], False)


def test_email_admin_requeue_changes_idempotency_key(client, capsys):
    from scripts.email_admin import retry
    m = queue_one()
    retry(m['id'], True)
    output = json.loads(capsys.readouterr().out)
    assert output['new_id'] != m['id']
    with db() as c:
        assert c.execute('SELECT count(*) FROM outbox').fetchone()[0] == 1
        assert c.execute('SELECT attempts FROM outbox WHERE id=?', (output['new_id'],)).fetchone()[0] == 0
