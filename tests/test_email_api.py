"""Transactional HTTPS email transport; providers mocked, not sent live."""
import httpx
from server import config, worker
from server.db import db, queue_mail


def queue_message():
    with db(True) as c:
        c.execute('DELETE FROM outbox')
        queue_mail(c, 'test-https-email', 'customer@example.com', 'Booking ready', 'Your booking details.')


def test_resend_https_email_success(owner, monkeypatch):
    queue_message()
    monkeypatch.setattr(config, 'RESEND_API_KEY', 're_test_not_a_real_key')
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={'id': 'test-message-id'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(worker.httpx, 'post', post)
    worker.deliver(limit=1)
    assert requests[0][0] == 'https://api.resend.com/emails'
    assert requests[0][1]['headers']['Idempotency-Key'].startswith('kontracts-mail/')
    assert requests[0][1]['json']['to'] == ['customer@example.com']
    assert requests[0][1]['json']['text'] == 'Your booking details.'
    with db() as c:
        message = c.execute('SELECT * FROM outbox').fetchone()
        assert message['sent_at'] is not None
        assert message['last_error'] is None


def test_resend_failure_preserves_outbox_for_retry(owner, monkeypatch):
    queue_message()
    monkeypatch.setattr(config, 'RESEND_API_KEY', 're_test_not_a_real_key')
    monkeypatch.setattr(worker.httpx, 'post', lambda url, **kwargs: httpx.Response(503, json={'error': 'unavailable'}, request=httpx.Request('POST', url)))
    worker.deliver(limit=1)
    with db() as c:
        message = c.execute('SELECT * FROM outbox').fetchone()
        assert message['sent_at'] is None
        assert message['attempts'] == 1
        assert message['last_error'] == 'HTTPStatusError'


def test_resend_missing_acceptance_id_not_marked_sent(owner, monkeypatch):
    queue_message()
    monkeypatch.setattr(config, 'RESEND_API_KEY', 're_test_not_a_real_key')
    monkeypatch.setattr(worker.httpx, 'post', lambda url, **kwargs: httpx.Response(200, json={}, request=httpx.Request('POST', url)))
    worker.deliver(limit=1)
    with db() as c:
        message = c.execute('SELECT * FROM outbox').fetchone()
        assert message['sent_at'] is None
        assert message['last_error'] == 'RuntimeError'
