"""Regression checks for structured Arabic A4 session report layouts."""
from test_app import client
from test_report_preliminary_print import create_draft, proof_count
from test_platform_v2 import post, signin, signout


def test_preliminary_print_uses_tables_and_preserves_security(client):
    token = create_draft(client)
    html = client.get("/platform/reports/1/print-draft").text
    assert '<table class="report-metadata"' in html
    assert html.count('<table class="report-content-table">') == 5
    assert '<div class="report-title">' in html
    assert 'الأهداف المسطرة' in html and 'التوصيات والإجراءات' in html
    assert 'أهداف التكوين' in html
    assert 'تقرير تجريبي &lt;اختبار&gt;' in html
    assert 'table-layout: fixed' in html
    assert 'white-space: pre-wrap' in html
    assert '@page { size: A4 portrait; margin: 12mm; }' in html
    assert 'رمز تحقق إلكتروني' not in html
    assert proof_count() == 0


def test_official_report_keeps_proof_and_tables(client):
    token = create_draft(client)
    assert post(client, "/platform/reports/1/submit", token).status_code == 303
    signout(client, token)
    coord = signin(client, "coord1", "strong-test-password-12345")
    assert post(client, "/platform/reports/1/review", coord,
                decision="approved").status_code == 303
    html = post(client, "/platform/reports/1/print", coord).text
    assert '<table class="report-metadata"' in html
    assert html.count('<table class="report-content-table">') == 5
    assert 'إثبات المصادقة الإلكترونية' not in html
    assert 'class="session-report-qr"' in html
    assert 'تقرير حصة معتمد إلكترونيًا' not in html
    assert 'رمز تحقق إلكتروني' in html
    assert '/static/noor_official_logo.webp' in html
    assert 'توقيع منسق المركز' not in html
    assert 'ختم الجمعية' not in html
    assert '@page { size: A4 portrait; margin: 12mm; }' in html
    assert proof_count() == 1
