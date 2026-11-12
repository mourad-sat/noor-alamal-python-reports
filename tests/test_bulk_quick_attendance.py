"""One-click roster attendance across both centers with strict safety checks."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def fixture_two_centers(client):
    admin = setup_org(client)
    for track_id, name in ((1, "متعلم لعيايدة أ"),
                           (1, "متعلم لعيايدة ب"),
                           (2, "متعلم بطانة")):
        response = post(
            client, "/platform/learners", admin,
            full_name=name, gender="ذكر", born_on="2012-01-01",
            birthplace="الرباط", year_id=1, track_id=track_id,
        )
        assert response.status_code == 303, response.text[:300]
    for track_id, weekday, start, end in (
        (1, 4, "08:00", "10:00"),
        (2, 4, "10:00", "12:00"),
    ):
        response = post(
            client, "/platform/schedule", admin,
            track_id=track_id, staff_id=3, subject="مهني",
            weekday=weekday, starts_at=start, ends_at=end,
            reason="برمجة الأسبوع للمركزين",
        )
        assert response.status_code == 303, response.text[:400]
    signout(client, admin)
    token = signin(client, "trainer", PASSWORD)
    for slot_id in (1, 2):
        result = post(
            client, "/platform/sessions", token,
            slot_id=slot_id, held_on="2026-11-06",
        )
        assert result.status_code == 303, result.text[:400]
    return token


def statuses():
    from app import engine
    from platform_v2_models import Attendance
    with Session(engine) as db:
        return {
            (mark.session_id, mark.enrollment_id): mark.status
            for mark in db.scalars(select(Attendance)).all()
        }


def test_fast_roster_shows_absent_only_defaults_and_requires_preview(client):
    fixture_two_centers(client)
    html = client.get("/platform/sessions/1").text
    assert "تسجيل الغائبين فقط" in html
    assert 'data-quick-attendance' in html
    assert "متعلم لعيايدة أ" in html
    assert "متعلم لعيايدة ب" in html
    assert "متعلم بطانة" not in html
    assert 'name="attendance_1"' in html
    assert 'name="attendance_2"' in html
    assert 'name="attendance_3"' not in html
    assert '<option value="present" selected>' in html
    assert 'data-quick-preview' in html
    assert 'data-quick-save disabled' in html
    assert not statuses(), "Opening attendance page must not save any record"


def test_batch_marks_everyone_with_explicit_confirmation_for_both_centers(client):
    token = fixture_two_centers(client)
    first = post(
        client, "/platform/sessions/1/attendance/bulk", token,
        confirm="1", attendance_1="absent", attendance_2="present",
    )
    assert first.status_code == 303, first.text[:400]
    assert first.headers["location"] == "/platform/sessions/1"
    second = post(
        client, "/platform/sessions/2/attendance/bulk", token,
        confirm="1", attendance_3="late",
    )
    assert second.status_code == 303, second.text[:400]
    assert statuses() == {
        (1, 1): "absent", (1, 2): "present", (2, 3): "late",
    }
    assert "متعلم لعيايدة أ" in client.get("/platform").text
    assert "متعلم بطانة" not in client.get("/platform/sessions/1").text
    assert "متعلم بطانة" in client.get("/platform/sessions/2").text
    # Repeating the form never duplicates or rewrites recorded attendance.
    repeat = post(
        client, "/platform/sessions/1/attendance/bulk", token,
        confirm="1", attendance_1="absent", attendance_2="present",
    )
    assert repeat.status_code == 422
    assert len(statuses()) == 3


def test_batch_validates_every_field_and_never_partially_saves(client):
    token = fixture_two_centers(client)
    path = "/platform/sessions/1/attendance/bulk"
    assert post(client, path, token,
                attendance_1="present", attendance_2="absent").status_code == 422
    assert post(client, path, token, confirm="1",
                attendance_1="present").status_code == 409
    assert post(client, path, token, confirm="1",
                attendance_1="present", attendance_2="present",
                attendance_3="absent").status_code == 409
    assert post(client, path, token, confirm="1",
                attendance_1="present", attendance_2="unknown").status_code == 422
    assert post(client, path, "fake-token", confirm="1",
                attendance_1="present", attendance_2="absent").status_code == 403
    assert not statuses(), "Invalid bulk form must not create partial marks"


def test_existing_attendance_is_immutable_to_batch_and_manual_route_still_works(client):
    token = fixture_two_centers(client)
    manual = post(
        client, "/platform/sessions/1/attendance", token,
        enrollment_id=1, status="absent",
    )
    assert manual.status_code == 303
    fail = post(
        client, "/platform/sessions/1/attendance/bulk", token,
        confirm="1", attendance_1="present", attendance_2="present",
    )
    assert fail.status_code == 409
    assert statuses() == {(1, 1): "absent"}
    success = post(
        client, "/platform/sessions/1/attendance/bulk", token,
        confirm="1", attendance_1="absent", attendance_2="late",
    )
    assert success.status_code == 303
    assert statuses() == {(1, 1): "absent", (1, 2): "late"}
    html = client.get("/platform/sessions/1").text
    assert 'name="attendance_1" value="absent"' in html
    assert 'name="attendance_2" value="late"' in html


def test_unresolved_absence_blocks_default_present_and_saves_no_unsafe_marks(client):
    token = fixture_two_centers(client)
    # First Friday: learner 1 was absent; next Friday in the same slot.
    assert post(client, "/platform/sessions/1/attendance", token,
                enrollment_id=1, status="absent").status_code == 303
    next_week = post(
        client, "/platform/sessions", token, slot_id=1,
        held_on="2026-11-13",
    )
    assert next_week.status_code == 303
    html = client.get("/platform/sessions/3").text
    assert "غياب سابق معلق" in html
    assert 'name="attendance_1"' in html
    rejected = post(
        client, "/platform/sessions/3/attendance/bulk", token, confirm="1",
        attendance_1="present", attendance_2="present",
    )
    assert rejected.status_code == 409
    assert statuses() == {(1, 1): "absent"}
    recorded = post(
        client, "/platform/sessions/3/attendance/bulk", token, confirm="1",
        attendance_1="unmarked", attendance_2="present",
    )
    assert recorded.status_code == 303
    assert statuses() == {(1, 1): "absent", (3, 2): "present"}


def test_bulk_restricted_to_assigned_instructor_and_locked_on_approval(client):
    token = fixture_two_centers(client)
    signout(client, token)
    coord = signin(client, "coord1", PASSWORD)
    no = post(
        client, "/platform/sessions/1/attendance/bulk", coord,
        confirm="1", attendance_1="present", attendance_2="present",
    )
    assert no.status_code == 403
    signout(client, coord)
    teacher = signin(client, "educator", PASSWORD)
    assert post(
        client, "/platform/sessions/1/attendance/bulk", teacher,
        confirm="1", attendance_1="present", attendance_2="present",
    ).status_code == 403
    signout(client, teacher)
    instructor = signin(client, "trainer", PASSWORD)
    saved = post(
        client, "/platform/sessions/1/report", instructor,
        subject="ورشة معتمدة", objectives="أهداف", activities="أنشطة",
    )
    assert saved.status_code == 303
    assert post(client, "/platform/reports/1/submit", instructor).status_code == 303
    signout(client, instructor)
    coord = signin(client, "coord1", PASSWORD)
    approved = post(
        client, "/platform/reports/1/review", coord,
        decision="approved", comment="",
    )
    assert approved.status_code == 303
    signout(client, coord)
    instructor = signin(client, "trainer", PASSWORD)
    assert "تسجيل الغائبين فقط" not in client.get("/platform/sessions/1").text
    assert post(
        client, "/platform/sessions/1/attendance/bulk", instructor,
        confirm="1", attendance_1="present", attendance_2="absent",
    ).status_code == 409
    assert not statuses()
