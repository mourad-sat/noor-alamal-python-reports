"""Role-limited dashboard absence names and real past-session summaries."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from test_app import client
from test_platform_v2 import setup_org, signin, signout, post

PASSWORD = "strong-test-password-12345"
CENTERS = ("دار الشباب لعيايدة 2", "مركز حمزة عبد المطلب")


def attendance_fixture(client):
    admin = setup_org(client)
    today = datetime.now(ZoneInfo("Africa/Casablanca")).date()
    missed = today - timedelta(days=7)
    # Both centers have an independently enrolled learner.
    signout(client, admin)
    coord1 = signin(client, "coord1", PASSWORD)
    enrolled = post(
        client, "/platform/learners", coord1,
        full_name="غائب لعيايدة", gender="ذكر", born_on="2012-01-01",
        birthplace="الرباط", phone="", address="", guardian_name="",
        guardian_relationship="الأب", guardian_phone="",
        year_id=1, track_id=1,
    )
    assert enrolled.status_code == 303
    signout(client, coord1)
    coord2 = signin(client, "coord2", PASSWORD)
    enrolled = post(
        client, "/platform/learners", coord2,
        full_name="غائب بطانة", gender="أنثى", born_on="2012-01-01",
        birthplace="سلا", phone="", address="", guardian_name="",
        guardian_relationship="الأب", guardian_phone="",
        year_id=1, track_id=2,
    )
    assert enrolled.status_code == 303
    signout(client, coord2)

    admin = signin(client, "admin", "correct-password")
    for track, time_start, time_end in ((1, "08:00", "10:00"),
                                        (2, "10:00", "12:00")):
        created = post(
            client, "/platform/schedule", admin,
            track_id=track, staff_id=3, subject="مهني",
            weekday=missed.weekday(), starts_at=time_start, ends_at=time_end,
            reason="حصص سابقة لتتبع الغياب",
        )
        assert created.status_code == 303, created.text[:400]
    signout(client, admin)
    staff = signin(client, "trainer", PASSWORD)
    for sid, eid in ((1, 1), (2, 2)):
        opened = post(client, "/platform/sessions", staff,
                      slot_id=sid, held_on=missed.isoformat())
        assert opened.status_code == 303, opened.text[:400]
        recorded = post(client, f"/platform/sessions/{sid}/attendance", staff,
                        enrollment_id=eid, status="absent")
        assert recorded.status_code == 303, recorded.text[:400]
    return staff, missed


def section(html):
    return html.split('class="pv2-neon-panel pv2-absence-panel"', 1)[1].split(
        '</section>', 1
    )[0]


def test_instructor_sees_past_absent_names_from_both_assigned_centers(client):
    token, missed = attendance_fixture(client)
    body = section(client.get("/platform").text)
    assert "الغائبون في حصصي السابقة" in body
    assert "غائب لعيايدة" in body
    assert "غائب بطانة" in body
    assert CENTERS[0] in body and CENTERS[1] in body
    assert missed.isoformat() in body
    assert "غياب دون إذن مسجّل" in body
    assert 'href="/platform/sessions/1"' in body
    assert 'href="/platform/sessions/2"' in body
    assert "سجلات الغياب المسجلة فقط" in body
    signout(client, token)
    signin(client, "educator", PASSWORD)
    other = section(client.get("/platform").text)
    assert "غائب لعيايدة" not in other
    assert "غائب بطانة" not in other
    assert "لا يوجد غياب مسجّل" in other


def test_coordinators_only_see_their_own_center_absentees(client):
    token, _ = attendance_fixture(client)
    signout(client, token)
    signin(client, "coord1", PASSWORD)
    first = section(client.get("/platform").text)
    assert "أسماء الغائبين في المركز" in first
    assert "غائب لعيايدة" in first
    assert "غائب بطانة" not in first
    assert 'action="/platform/learners/1/permit"' in first
    assert 'action="/platform/learners/2/permit"' not in first
    csrf = __import__("test_platform_v2").csrf_of(client)
    signout(client, csrf)
    signin(client, "coord2", PASSWORD)
    second = section(client.get("/platform").text)
    assert "غائب بطانة" in second
    assert "غائب لعيايدة" not in second
    assert 'action="/platform/learners/2/permit"' in second
    assert 'action="/platform/learners/1/permit"' not in second


def test_admin_sees_center_totals_and_absentees_in_actionable_table(client):
    token, _ = attendance_fixture(client)
    signout(client, token)
    signin(client, "admin", "correct-password")
    body = section(client.get("/platform").text)
    assert "غياب المستفيدين حسب المركز" in body
    assert CENTERS[0] in body and CENTERS[1] in body
    assert body.count('class="pv2-absence-count">1</span>') == 2
    assert body.count("1 مستفيد مختلف") == 2
    assert "غائب لعيايدة" in body
    assert "غائب بطانة" in body
    assert "جدول الغائبين حسب المركز" in body
    assert "اسم المستفيد" in body
    assert "المسلك" in body
    assert "حصة الغياب" in body
    assert "تاريخ الغياب" in body
    assert 'action="/platform/learners/1/permit"' in body
    assert 'action="/platform/learners/2/permit"' in body
    assert "العدد يمثل حالات الغياب" in body


def test_absence_status_changes_reflected_without_stale_snapshot(client):
    from app import engine
    from platform_v2_models import Attendance, Permit
    token, _ = attendance_fixture(client)
    with Session(engine) as db:
        first = db.get(Attendance, 1)
        db.add(Permit(
            learner_id=1, center_id=1, reason="إذن طبي مسجل",
            issued_by="المنسق"
        ))
        db.flush()
        first.permit_id = db.query(Permit.id).first()[0]
        db.commit()
    body = section(client.get("/platform").text)
    assert "غياب بإذن" in body
    assert "غياب دون إذن مسجّل" in body


def test_dashboard_does_not_count_future_or_canceled_sessions(client):
    from app import engine
    from platform_v2_models import SessionDay
    token, missed = attendance_fixture(client)
    with Session(engine) as db:
        item = db.get(SessionDay, 2)
        item.status = "canceled"
        db.commit()
    teacher = section(client.get("/platform").text)
    assert "غائب لعيايدة" in teacher
    assert "غائب بطانة" not in teacher
    assert 'class="pv2-absence-total">1</span>' in teacher
