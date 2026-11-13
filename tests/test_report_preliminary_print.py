"""Two mutually exclusive report print paths, including approval-proof safety."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_app import client
from test_platform_v2 import post, setup_org, signin, signout


def create_draft(client):
    admin_csrf = setup_org(client)
    created = post(
        client, "/platform/schedule", admin_csrf, track_id=1,
        staff_id=3, subject="مهني", weekday=4,
        starts_at="08:00", ends_at="10:00", reason="برمجة أسبوعية معتمدة"
    )
    assert created.status_code == 303
    signout(client, admin_csrf)
    csrf = signin(client, "trainer", "strong-test-password-12345")
    opened = post(client, "/platform/sessions", csrf, slot_id=1, held_on="2026-11-06")
    assert opened.status_code == 303
    saved = post(
        client, "/platform/sessions/1/report", csrf,
        subject="تقرير تجريبي <اختبار>",
        objectives="أهداف التكوين", activities="أنشطة تطبيقية",
        results="نتائج إيجابية", challenges="ملاحظات أولية",
        recommendations="مواصلة الدعم",
    )
    assert saved.status_code == 303
    return csrf


def proof_count():
    from app import engine
    from platform_v2_models import SessionReportProof
    with Session(engine) as db:
        return db.scalar(select(func.count(SessionReportProof.id))) or 0


def test_draft_is_printable_as_unapproved_without_proof_or_qr(client):
    create_draft(client)
    listing = client.get("/platform/reports").text
    session = client.get("/platform/sessions/1").text
    assert 'href="/platform/reports/1/print-draft"' in listing
    assert 'href="/platform/reports/1/print-draft"' in session
    before = proof_count()
    response = client.get("/platform/reports/1/print-draft")
    assert response.status_code == 200, response.text[:500]
    assert "نسخة أولية غير معتمدة — ليست وثيقة رسمية" in response.text
    assert "تقرير تجريبي &lt;اختبار&gt;" in response.text
    assert "<اختبار>" not in response.text
    assert "أهداف التكوين" in response.text
    assert "أنشطة تطبيقية" in response.text
    assert "عدد الحاضرين" in response.text
    assert "رمز تحقق إلكتروني" not in response.text
    assert 'class="qr"' not in response.text
    assert "/verify/" not in response.text
    assert "إثبات المصادقة الإلكترونية" not in response.text
    assert proof_count() == before
    assert "no-store" in response.headers["cache-control"]


def test_submitted_report_remains_preliminary_until_coordinator_approves(client):
    token = create_draft(client)
    assert post(client, "/platform/reports/1/submit", token).status_code == 303
    submitted = client.get("/platform/reports/1/print-draft")
    assert submitted.status_code == 200
    assert "مرسل للمراجعة" in submitted.text
    assert "نسخة أولية غير معتمدة" in submitted.text
    official_denied = post(client, "/platform/reports/1/print", token)
    assert official_denied.status_code == 409
    signout(client, token)
    coordinator = signin(client, "coord1", "strong-test-password-12345")
    assert client.get("/platform/reports/1/print-draft").status_code == 200
    approved = post(
        client, "/platform/reports/1/review", coordinator,
        decision="approved", comment=""
    )
    assert approved.status_code == 303
    assert client.get("/platform/reports/1/print-draft").status_code == 409
    listing = client.get("/platform/reports").text
    assert 'action="/platform/reports/1/print"' in listing
    assert 'href="/platform/reports/1/print-draft"' not in listing
    before = proof_count()
    official = post(client, "/platform/reports/1/print", coordinator)
    assert official.status_code == 200
    assert "تقرير حصة معتمد إلكترونيًا" not in official.text
    assert "إثبات المصادقة الإلكترونية" not in official.text
    assert 'class="session-report-qr"' in official.text
    assert "رمز تحقق إلكتروني" in official.text
    assert proof_count() == before + 1


def test_unapproved_print_does_not_expose_other_center_or_employee(client):
    token = create_draft(client)
    signout(client, token)
    educator = signin(client, "educator", "strong-test-password-12345")
    assert client.get("/platform/reports/1/print-draft").status_code == 403
    signout(client, educator)
    coord = signin(client, "coord2", "strong-test-password-12345")
    assert client.get("/platform/reports/1/print-draft").status_code == 403
    signout(client, coord)
    admin = signin(client, "admin", "correct-password")
    assert client.get("/platform/reports/1/print-draft").status_code == 200
    assert client.get("/platform/reports/99999/print-draft").status_code == 404


def test_returned_report_displays_review_comment_without_official_proof(client):
    token = create_draft(client)
    assert post(client, "/platform/reports/1/submit", token).status_code == 303
    signout(client, token)
    coord = signin(client, "coord1", "strong-test-password-12345")
    returned = post(
        client, "/platform/reports/1/review", coord,
        decision="returned", comment="يرجى تصحيح عنوان الأنشطة"
    )
    assert returned.status_code == 303
    signout(client, coord)
    signin(client, "trainer", "strong-test-password-12345")
    result = client.get("/platform/reports/1/print-draft")
    assert result.status_code == 200
    assert "أُعيد للتصحيح" in result.text
    assert "يرجى تصحيح عنوان الأنشطة" in result.text
    assert "نسخة أولية غير معتمدة" in result.text
    assert proof_count() == 0
