"""Individual learner tracking: scoped access, real attendance, approved grades and A4 printing."""
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def populate(client):
    admin = setup_org(client)
    first = post(client, "/platform/learners", admin,
                 full_name="مستفيد من لعيايدة", year_id=1, track_id=1,
                 guardian_phone="0612345678")
    second = post(client, "/platform/learners", admin,
                  full_name="مستفيد من بطانة", year_id=1, track_id=2,
                  guardian_phone="0699999999")
    assert first.status_code == second.status_code == 303
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني",
                weekday=4, starts_at="08:00", ends_at="10:00",
                reason="برنامج التكوين").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/attendance", teacher,
                enrollment_id=1, status="present").status_code == 303
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-13").status_code == 303
    assert post(client, "/platform/sessions/2/attendance", teacher,
                enrollment_id=1, status="absent").status_code == 303
    assert post(client, "/platform/assessments", teacher,
                track_id=1, subject="مهني", title="اختبار أول",
                held_on="2026-11-06", coefficient="2").status_code == 303
    assert post(client, "/platform/assessments", teacher,
                track_id=1, subject="مهني", title="اختبار ثان",
                held_on="2026-11-13", coefficient="1").status_code == 303
    assert post(client, "/platform/grades", teacher,
                assessment_id=1, enrollment_id=1, score="16").status_code == 303
    assert post(client, "/platform/grades", teacher,
                assessment_id=2, enrollment_id=1, score="5").status_code == 303
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    assert post(client, "/platform/grades/1/approve", coord).status_code == 303
    return coord


def test_card_summary_print_approved_grades_only_and_no_contacts(client):
    csrf = populate(client)
    listing = client.get("/platform/learners?track_id=1")
    assert '/platform/learners/1/progress' in listing.text
    detail = client.get("/platform/learners/1")
    assert "فتح بطاقة التتبع الفردي" in detail.text

    card = client.get("/platform/learners/1/progress")
    assert card.status_code == 200
    for expected in (
        "بطاقة التتبع الفردي للمستفيد", "مستفيد من لعيايدة",
        "مؤشرات الحضور والغياب", "تطور المواظبة حسب الشهر",
        "نتائج التقييمات المعتمدة", "تفاصيل الحضور والغياب",
        "تفاصيل التقييمات المعتمدة", "اختبار أول", "16.00",
        "حاضر", "غائب", "2026-11", "50.0%",
    ):
        assert expected in card.text, expected
    assert "اختبار ثان" not in card.text
    assert "0699999999" not in card.text
    assert "0612345678" not in card.text
    assert "مستفيد من بطانة" not in card.text

    printed = client.get("/platform/learners/1/progress/print")
    assert printed.status_code == 200
    assert "size:A4 portrait" in printed.text
    assert "window.print()" in printed.text
    assert "جمعية نور الأمل" in printed.text
    assert "no-store" in printed.headers["cache-control"]
    assert "اختبار ثان" not in printed.text

    # No data manipulation performed by rendering the cards.
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Attendance, Grade
    with Session(engine) as db:
        assert db.get(Grade, 2).status == "pending"
        assert db.get(Attendance, 2).status == "absent"
    signout(client, csrf)


def test_tracking_is_scoped_to_center_and_instructor_assignments(client):
    coord = populate(client)
    assert client.get("/platform/learners/2/progress").status_code == 403
    assert client.get("/platform/learners/2/progress/print").status_code == 403
    signout(client, coord)
    other = signin(client, "coord2", PASSWORD)
    assert client.get("/platform/learners/1/progress").status_code == 403
    assert client.get("/platform/learners/1/progress/print").status_code == 403
    assert client.get("/platform/learners/2/progress").status_code == 200
    signout(client, other)
    trainer = signin(client, "trainer", PASSWORD)
    allowed = client.get("/platform/learners/1/progress")
    assert allowed.status_code == 200
    assert "0612345678" not in allowed.text
    assert client.get("/platform/learners/1/progress/print").status_code == 200
    signout(client, trainer)
    assert client.get("/platform/learners/1/progress").status_code == 303
    assert client.get("/platform/learners/1/progress/print").status_code == 303


def test_date_filters_and_unknown_ids(client):
    populate(client)
    november = client.get("/platform/learners/1/progress"
                          "?start=2026-11-01&end=2026-11-07")
    assert november.status_code == 200
    assert "2026-11-06" in november.text
    assert "2026-11-13" not in november.text
    assert "100.0%" in november.text
    assert client.get("/platform/learners/1/progress"
                      "?start=2026-11-15&end=2026-11-01").status_code == 422
    assert client.get("/platform/learners/1/progress"
                      "?start=2025-11-01&end=2025-11-30").status_code == 422
    assert client.get("/platform/learners/999/progress").status_code == 404
