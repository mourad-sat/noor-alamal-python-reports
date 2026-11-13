"""Role-scoped pending absence dashboard and direct admission permits."""
from sqlalchemy.orm import Session
from sqlalchemy import select
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def test_pending_absent_learners_can_get_permits_from_admin_dashboard(client):
    admin = setup_org(client)
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد غائب تجريبي", year_id=1, track_id=1).status_code == 303
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="حصة أسبوعية للمستفيدين").status_code == 303
    assert "مستفيد غائب تجريبي" not in client.get("/platform").text
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/attendance", teacher,
                enrollment_id=1, status="absent").status_code == 303
    html = client.get("/platform").text
    assert "الغائبون الذين ينتظرون إذن الدخول" not in html
    signout(client, teacher)
    coord_other = signin(client, "coord2", PASSWORD)
    assert "مستفيد غائب تجريبي" not in client.get("/platform").text
    assert post(client, "/platform/learners/1/permit", coord_other,
                reason="تبرير الغياب والسماح بالدخول").status_code == 403
    signout(client, coord_other)
    admin = signin(client, "admin", "correct-password")
    html = client.get("/platform").text
    assert "مستفيد غائب تجريبي" in html
    assert 'action="/platform/learners/1/permit"' in html
    assert "حصص غياب معلقة" in html
    assert post(client, "/platform/learners/1/permit", admin,
                reason="قصير").status_code == 422
    assert post(client, "/platform/learners/1/permit", admin,
                reason="تمت مراجعة الغياب والموافقة على استئناف الحضور").status_code == 303
    refreshed = client.get("/platform").text
    assert "مستفيد غائب تجريبي" in refreshed
    assert "غياب بإذن" in refreshed
    assert 'action="/platform/learners/1/permit"' not in refreshed
    assert "مغطى بإذن" in client.get("/platform/learners/1").text


def test_coordinator_has_direct_pending_absence_button(client):
    admin = setup_org(client)
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد في المركز الأول", year_id=1, track_id=1).status_code == 303
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00", reason="حصة أسبوعية").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/attendance", teacher,
                enrollment_id=1, status="absent").status_code == 303
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    html = client.get("/platform").text
    assert "مستفيد في المركز الأول" in html
    assert 'action="/platform/learners/1/permit"' in html
    assert post(client, "/platform/learners/1/permit", coord,
                reason="إذن إداري مبرر لاستئناف الحضور").status_code == 303
