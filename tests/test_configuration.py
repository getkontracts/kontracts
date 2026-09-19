"""Startup/config regressions; subprocesses never use the operator's .env."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def probe(tmp_path):
    root = tmp_path / 'isolated-app'
    server = root / 'server'
    server.mkdir(parents=True)
    (server / '__init__.py').write_text('')
    for name in ('config.py', 'mail.py'):
        shutil.copy2(ROOT / 'server' / name, server / name)

    def run(values=None, dotenv='', production=False):
        if dotenv:
            (root / '.env').write_text(dotenv, encoding='utf-8-sig')
        env = {'PATH': os.environ.get('PATH', os.defpath), 'PYTHONPATH': str(root),
               'APP_SECRET': 'isolated-test-secret-' * 4, 'DATA_DIR': './data'}
        env.update(values or {})
        script = ''
        if production:
            # Exercise production configuration, not the machine's package gate.
            script = "import importlib.metadata; importlib.metadata.version=lambda name:'999.0.0';"
        script += "from server import config as c; import json; print(json.dumps({k:getattr(c,k) for k in ['BASE_URL','DATA','DB','MAIL_ENABLED','MAIL_SENDER_DOMAIN','MAIL_ACCOUNTS_ADDRESS','MAIL_NOTIFICATIONS_ADDRESS','MAIL_BILLING_ADDRESS','SUPPORT_EMAIL','MAIL_REPLY_TO']}, default=str))"
        return root, subprocess.run([sys.executable, '-c', script], env=env, cwd=tmp_path,
                                    capture_output=True, text=True, timeout=15)
    return run


def test_fresh_config_uses_four_distinct_new_domain_identities(probe):
    root, result = probe()
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['MAIL_SENDER_DOMAIN'] == 'getkontracts.app'
    assert [data[key] for key in ('MAIL_ACCOUNTS_ADDRESS', 'MAIL_NOTIFICATIONS_ADDRESS',
                                 'MAIL_BILLING_ADDRESS', 'SUPPORT_EMAIL')] == [
        role + '@getkontracts.app' for role in ('accounts', 'notifications', 'billing', 'support')]
    assert data['MAIL_REPLY_TO'] == 'support@getkontracts.app'
    assert data['BASE_URL'] == 'http://localhost:8000'
    assert data['MAIL_ENABLED'] is True
    assert data['DATA'] == str(root / 'data')
    assert data['DB'] == str(root / 'data' / 'kontracts.sqlite3')


def test_utf8_dotenv_loads_and_process_environment_wins(probe):
    _, result = probe({'SUPPORT_EMAIL': 'operator@example.com'},
                      dotenv='SUPPORT_EMAIL=file@example.com\nMAIL_ENABLED=false\n')
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['SUPPORT_EMAIL'] == data['MAIL_REPLY_TO'] == 'operator@example.com'
    assert data['MAIL_ENABLED'] is False


def test_legacy_sender_remains_supported(probe):
    _, result = probe({'MAIL_FROM': 'Legacy <mail@example.com>'})
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['MAIL_ACCOUNTS_ADDRESS'] == data['MAIL_NOTIFICATIONS_ADDRESS'] == data['MAIL_BILLING_ADDRESS'] == 'mail@example.com'
    assert data['SUPPORT_EMAIL'] == 'support@example.com'


def test_mixed_sender_domains_fail_clearly(probe):
    _, result = probe({'MAIL_NOTIFICATIONS_FROM': 'Notifications <mail@example.com>'})
    assert result.returncode != 0
    assert 'All three app senders must use MAIL_SENDER_DOMAIN' in result.stderr


def test_relative_database_override_is_rooted_at_app(probe):
    root, result = probe({'DATABASE_PATH': 'data/existing.sqlite3'})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['DB'] == str(root / 'data' / 'existing.sqlite3')


def test_production_refuses_silently_disabled_mail(probe):
    _, result = probe({'APP_ENV': 'production', 'MAIL_ENABLED': 'false'}, production=True)
    assert result.returncode != 0
    assert 'Production requires MAIL_ENABLED=true' in result.stderr


def test_production_default_origin_uses_new_domain(probe, tmp_path):
    policy = tmp_path / 'test-policy.txt'
    policy.write_text('Synthetic configuration test text only. ' * 12)
    _, result = probe({'APP_ENV': 'production', 'RESEND_API_KEY': 'test-not-live',
                      'LEGAL_NAME': 'Test Operator', 'LEGAL_ADDRESS': 'Test Address',
                      'LEGAL_TERMS_PATH': str(policy), 'LEGAL_PRIVACY_PATH': str(policy),
                      'LEGAL_REVIEW_CONFIRMED': 'true'}, production=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['BASE_URL'] == 'https://getkontracts.app'


def test_one_environment_template_without_conflicting_email_keys():
    text = (ROOT / '.env.example').read_text()
    lines = [line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#')]
    assert len(lines) == len(dict(lines))
    values = dict(lines)
    assert values['MAIL_ENABLED'] == 'true'
    assert values['MAIL_SENDER_DOMAIN'] == 'getkontracts.app'
    assert values['RESEND_API_KEY'] == ''
    assert 'MAIL_FROM' not in values
    assert not (ROOT / 'email.env.example').exists()
    assert not (ROOT / 'config' / 'email.env.example').exists()


@pytest.mark.parametrize(('enabled', 'key', 'expected'), [
    (False, 'test-not-live', 'disabled'), (True, '', 'local-eml'), (True, 'test-not-live', 'provider'),
])
def test_setup_reports_real_delivery_mode(owner, monkeypatch, enabled, key, expected):
    from server import config
    monkeypatch.setattr(config, 'MAIL_ENABLED', enabled)
    monkeypatch.setattr(config, 'RESEND_API_KEY', key)
    monkeypatch.setattr(config, 'SMTP_HOST', '')
    platform = owner.get('/api/setup').json()['platform']
    assert platform['email_mode'] == expected
    assert platform['email_enabled'] is enabled
    assert platform['email_configured'] is (enabled and bool(key))


@pytest.mark.parametrize('valid_contact', [True, False])
def test_manage_api_exposes_validated_business_contact(owner, valid_contact):
    from server import config, core
    from server.db import db, packed
    from server.security import decrypt
    from tests.test_app import shop
    from tests.test_workflow import request_job
    booking = request_job(owner)
    business = shop()
    contact = core.settings(business)['contact_email']
    with db(True) as c:
        row = c.execute('SELECT * FROM bookings WHERE id=?', (booking['id'],)).fetchone()
        raw = decrypt(row['manage_encrypted'])
        if not valid_contact:
            settings = core.settings(business)
            settings['contact_email'] = 'not-an-email'
            c.execute('UPDATE shops SET settings=? WHERE id=?', (packed(settings), business['id']))
    response = owner.get('/api/public/manage/' + raw)
    assert response.status_code == 200
    assert response.json()['shop']['correspondence_email'] == (contact if valid_contact else config.SUPPORT_EMAIL)
