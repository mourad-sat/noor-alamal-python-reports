"""Admin draft approvals and simplified attendance UI regression tests."""
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout
from pathlib import Path

PASSWORD = "strong-test-password-12345"


def seed_report(client, *, complete=True):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="حصة تعليمية معتمدة").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-06").status_code == 303
    fields = dict(subject="ورشة تطبيقية", objectives="تمكين المستفيدين من المهارات",
                  activities="تمارين تطبيقية داخل الورشة")
    if not complete:
        fields["objectives"] = ""
    assert post(client, "/platform/sessions/1/report", teacher, **fields).status_code == 303
    return teacher


def test_administration_can_approve_complete_draft_without_submission(client):
    teacher = seed_report(client)
    page = client.get("/platform/reports").text
    assert "اعتماد المسودة مباشرة" not in page
    signout(client, teacher)
    admin = signin(client, "admin", "correct-password")
    page = client.get("/platform/reports").text
    assert "اعتماد المسودة مباشرة" in page
    approved = post(client, "/platform/reports/1/review", admin,
                    decision="approved")
    assert approved.status_code == 303
    assert "اعتماد المسودة مباشرة" not in client.get("/platform/reports").text
    from app import engine
    from platform_v2_models import SessionReport
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        report = db.get(SessionReport, 1)
        assert report.status == "approved"
        assert report.reviewed_by == "إدارة الجمعية"


def test_incomplete_draft_cannot_be_approved(client):
    teacher = seed_report(client, complete=False)
    signout(client, teacher)
    admin = signin(client, "admin", "correct-password")
    assert post(client, "/platform/reports/1/review", admin,
                decision="approved").status_code == 422


def test_coordinator_cannot_approve_draft_before_submission(client):
    teacher = seed_report(client)
    signout(client, teacher)
    coordinator = signin(client, "coord1", PASSWORD)
    assert "اعتماد المسودة مباشرة" not in client.get("/platform/reports").text
    assert post(client, "/platform/reports/1/review", coordinator,
                decision="approved").status_code == 409


def test_attendance_individual_action_is_secondary(client):
    seed_report(client)
    markup = client.get("/platform/sessions/1").text
    assert "تسجيل الغائبين فقط" in markup
    assert '<details class="pv2-attendance-detail-toggle">' in markup
    assert "تفاصيل الحضور والتسجيل الفردي (عند الحاجة)" in markup
    assert 'data-quick-attendance' in markup
    assert 'action="/platform/sessions/1/attendance"' not in markup or "pv2-attendance-detail-toggle" in markup


def test_dark_report_editor_has_explicit_contrasting_text():
    css = (Path(__file__).resolve().parents[1] / "static/platform_ui.css").read_text()
    assert '.pv2-session-report-form .pv2-report-field textarea' in css
    assert '-webkit-text-fill-color:#f3f8ff' in css
    assert 'caret-color:#69def2' in css
