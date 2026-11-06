import hashlib
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'testing.sqlite3'))
    monkeypatch.setenv('APP_SECRET', 'local-testing-session-key-longer-than-32-characters')
    digest = hashlib.pbkdf2_hmac('sha256', b'correct-password', b'0123456789abcdef', 390000)
    monkeypatch.setenv('ADMIN_PASSWORD_HASH', 'pbkdf2_sha256$390000$' + b'0123456789abcdef'.hex() + '$' + digest.hex())
    monkeypatch.setenv('ADMIN_USERNAME', 'admin')
    monkeypatch.delenv('VERCEL', raising=False)
    sys.path.insert(0, str(PROJECT))
    for name in ('platform_v2', 'platform_v2_models', 'platform_v2_imports', 'platform_v2_exports', 'platform_v2_aggregates', 'app'):
        sys.modules.pop(name, None)
    application = importlib.import_module('app')
    with TestClient(application.app, follow_redirects=False) as client:
        yield client


def login(client):
    response = client.post('/login', data={'username': 'admin', 'password': 'correct-password'})
    assert response.status_code == 303 and response.headers['location'] == '/'
    cookie = client.cookies.get('session')
    assert cookie
    from itsdangerous import TimestampSigner
    # Obtain the anti-CSRF input from the rendered form.
    import re
    html = client.get('/reports/new').text
    match = re.search('name="_csrf" value="([^"]+)', html)
    assert match
    return match.group(1)


def test_authentication_and_access_control(client):
    assert client.get('/').status_code == 303
    assert client.get('/export/reports.csv').status_code == 303
    assert client.get('/login').status_code == 200
    assert 'مرحباً بعودتك' in client.post('/login', data={'username': 'admin', 'password': 'wrong'}).text
    csrf = login(client)
    assert 'لوحة القيادة' in client.get('/').text
    assert client.post('/logout', data={'_csrf': 'bad'}).status_code == 403
    assert client.post('/logout', data={'_csrf': csrf}).status_code == 303
    assert client.get('/').status_code == 303


def test_report_workflow_and_exports(client):
    csrf = login(client)
    report = dict(kind='training', session_date='2026-11-05', subject='تقرير الحصة التجريبية',
                  group_name='الفوج الأول', trainer_name='المؤطر', duration_minutes='90',
                  present_count='16', absent_count='2', objectives='تحسين المهارات',
                  activities='أنشطة عملية', results='مخرجات ناجحة', _csrf=csrf)
    assert client.post('/reports', data={**report, '_csrf': 'bad'}).status_code == 403
    response = client.post('/reports', data=report)
    assert response.status_code == 303
    link = response.headers['location']
    assert 'تقرير الحصة التجريبية' in client.get(link).text
    assert 'تقرير الحصة التجريبية' in client.get('/reports?kind=training').text
    assert 'تحسين المهارات' in client.get(link).text
    csv = client.get('/export/reports.csv')
    assert csv.status_code == 200
    assert 'تقرير الحصة التجريبية' in csv.content.decode('utf-8-sig')
    report_id = int(link.split('/')[2].split('?')[0])
    changed = {**report, 'subject': 'عنوان معدّل'}
    assert client.post(f'/reports/{report_id}/edit', data=changed).status_code == 303
    assert 'عنوان معدّل' in client.get(f'/reports/{report_id}').text
    assert client.post(f'/reports/{report_id}/delete', data={'_csrf': csrf}).status_code == 303
    assert client.get(f'/reports/{report_id}').status_code == 404


def test_absences_and_print_summary(client):
    csrf = login(client)
    response = client.post('/absences', data={
        '_csrf': csrf, 'learner_name': 'اسم تجريبي', 'group_name': 'الفوج 2',
        'absence_date': '2026-11-04', 'reason': 'مرض', 'justified': 'true', 'notes': 'ملاحظة',
    })
    assert response.status_code == 303
    assert 'اسم تجريبي' in client.get('/absences').text
    summary = client.get('/absences/summary/print?start=2026-11-01&end=2026-11-30')
    assert summary.status_code == 200
    assert 'كشف الغياب' in summary.text
    assert 'اسم تجريبي' in summary.text
    assert 'اسم تجريبي' in client.get('/export/absences.csv').content.decode('utf-8-sig')


def test_csv_formula_safety(client):
    login(client)
    from app import safe_csv
    assert safe_csv('=1+1') == "'=1+1"
    assert safe_csv('عربي') == 'عربي'


def test_health_checks_database(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'service': 'noor-alamal', 'status': 'ready'}
