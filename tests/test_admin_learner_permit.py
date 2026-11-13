"""Ensure administration can see the same absence permit action as coordinator."""
from test_app import client
from test_platform_v2 import setup_org, signin, signout, post


def test_admin_sees_absence_permit_for_pending_absence(client):
    admin = setup_org(client)
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد تجريبي للإذن", year_id=1, track_id=1).status_code == 303
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="الجدولة الرسمية للحصة").status_code == 303
    signout(client, admin)
    staff = signin(client, "trainer", "strong-test-password-12345")
    assert post(client, "/platform/sessions", staff,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/attendance", staff,
                enrollment_id=1, status="absent").status_code == 303
    signout(client, staff)
    admin = signin(client, "admin", "correct-password")
    html = client.get("/platform/learners/1").text
    assert "إذن دخول معلل يغطي الغيابات المعلقة" in html
    assert post(client, "/platform/learners/1/permit", admin,
                reason="السماح باستئناف الحضور بعد تبرير الغياب").status_code == 303
    assert "مغطى بإذن" in client.get("/platform/learners/1").text
