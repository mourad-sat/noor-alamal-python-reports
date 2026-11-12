"""Regression tests: learner track transfer is auditable and scoped."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

URL = "/platform/learners/1/transfer"
PASS = "strong-test-password-12345"


def test_admin_can_transfer_within_center_and_keeps_enrollment_id(client):
    admin = setup_org(client)
    assert post(client, "/platform/tracks", admin,
                year_id=1, center_id=1, name="التصميم وبناء الهوية").status_code == 303
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد النقل التجريبي", year_id=1, track_id=1).status_code == 303
    page = client.get("/platform/learners/1")
    assert 'href="/platform/learners/1/transfer"' in page.text
    assert client.get(URL).status_code == 200
    assert post(client, URL, admin, track_id=3,
                reason="تغيير المسلك بناء على رغبة المستفيد",
                confirmation="نعم").status_code == 303
    from app import engine
    from platform_v2_models import Audit, Enrollment
    with Session(engine) as db:
        enrollment = db.get(Enrollment, 1)
        assert enrollment.id == 1 and enrollment.track_id == 3
        audit = db.scalar(select(Audit).where(Audit.action == "transfer_learner_track"))
        assert audit and "from_track_id=1" in audit.reason
        assert "to_track_id=3" in audit.reason


def test_transfer_rejects_cross_center_same_track_missing_reason_and_staff(client):
    admin = setup_org(client)
    assert post(client, "/platform/tracks", admin,
                year_id=1, center_id=1, name="التنشيط وإقامة الفعاليات").status_code == 303
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد آخر للتجربة", year_id=1, track_id=1).status_code == 303
    assert post(client, URL, admin, track_id=2, reason="سبب واضح للنقل",
                confirmation="نعم").status_code == 422
    assert post(client, URL, admin, track_id=1, reason="سبب واضح للنقل",
                confirmation="نعم").status_code == 422
    assert post(client, URL, admin, track_id=3, reason="قصير",
                confirmation="نعم").status_code == 422
    assert post(client, URL, admin, track_id=3, reason="سبب واضح للنقل",
                confirmation="").status_code == 422
    signout(client, admin)
    staff = signin(client, "trainer", PASS)
    assert client.get(URL).status_code == 403
    assert post(client, URL, staff, track_id=3,
                reason="سبب واضح للنقل", confirmation="نعم").status_code == 403
    signout(client, staff)
    coord = signin(client, "coord2", PASS)
    assert client.get(URL).status_code == 403
    assert post(client, URL, coord, track_id=3,
                reason="سبب واضح للنقل", confirmation="نعم").status_code == 403


def test_transfer_protects_existing_attendance_history(client):
    admin = setup_org(client)
    assert post(client, "/platform/tracks", admin,
                year_id=1, center_id=1, name="مسلك جديد").status_code == 303
    assert post(client, "/platform/learners", admin,
                full_name="مستفيد له تاريخ حضور", year_id=1, track_id=1).status_code == 303
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="جدولة الحصص الرسمية").status_code == 303
    signout(client, admin)
    instructor = signin(client, "trainer", PASS)
    assert post(client, "/platform/sessions", instructor, slot_id=1,
                held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/attendance", instructor,
                enrollment_id=1, status="present").status_code == 303
    signout(client, instructor)
    admin = signin(client, "admin", "correct-password")
    result = post(client, URL, admin, track_id=3,
                  reason="طلب تغيير المسلك بعد التسجيل",
                  confirmation="نعم")
    assert result.status_code == 409
    from app import engine
    from platform_v2_models import Enrollment
    with Session(engine) as db:
        assert db.get(Enrollment, 1).track_id == 1
