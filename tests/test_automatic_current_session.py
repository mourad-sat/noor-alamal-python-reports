"""Automatic lesson selection cannot invent activity or cross center permissions."""
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from test_app import client
from test_platform_v2 import setup_org, post, signin, signout
import platform_v2


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 12, 9, 0, tzinfo=tz)


def add_thursday_slot(client, csrf, track, start, end):
    response = post(
        client, "/platform/schedule", csrf,
        track_id=track, staff_id=3, subject="مهني",
        weekday=3, starts_at=start, ends_at=end,
        reason="توقيت الحصة الأسبوعية للمركز",
    )
    assert response.status_code == 303, response.text[:400]


def session_count():
    from app import engine
    from platform_v2_models import SessionDay
    with Session(engine) as db:
        return db.scalar(select(func.count(SessionDay.id))) or 0


def test_current_session_identifies_today_by_staff_and_local_time(client, monkeypatch):
    monkeypatch.setattr(platform_v2, "datetime", FrozenClock)
    admin = setup_org(client)
    add_thursday_slot(client, admin, 1, "08:30", "10:30")
    add_thursday_slot(client, admin, 2, "11:00", "13:00")
    signout(client, admin)
    signin(client, "trainer", "strong-test-password-12345")
    assert session_count() == 0
    response = client.get("/platform/sessions/today")
    assert response.status_code == 200, response.text[:400]
    assert "حصصي اليوم" in response.text
    assert "الحصة الجارية الآن" in response.text
    assert "حصة لاحقة اليوم" in response.text
    assert "دار الشباب لعيايدة 2" in response.text
    assert "مركز حمزة عبد المطلب" in response.text
    assert 'name="slot_id" value="1"' in response.text
    assert 'name="slot_id" value="2"' in response.text
    assert 'name="held_on" value="2026-11-12"' in response.text
    assert response.text.index('name="slot_id" value="1"') < response.text.index(
        'name="slot_id" value="2"')
    assert session_count() == 0, "GET must never create an attendance session"
    assert 'href="/platform/sessions/today"' in client.get("/platform").text
    assert 'href="/platform/sessions/today"' in client.get("/platform/reports").text


def test_opened_current_session_redirects_automatically_to_existing(client, monkeypatch):
    monkeypatch.setattr(platform_v2, "datetime", FrozenClock)
    admin = setup_org(client)
    add_thursday_slot(client, admin, 1, "08:30", "10:30")
    signout(client, admin)
    staff_csrf = signin(client, "trainer", "strong-test-password-12345")
    opened = post(client, "/platform/sessions", staff_csrf,
                  slot_id=1, held_on="2026-11-12")
    assert opened.status_code == 303
    assert opened.headers["location"] == "/platform/sessions/1"
    assert session_count() == 1
    result = client.get("/platform/sessions/today")
    assert result.status_code == 303
    assert result.headers["location"] == "/platform/sessions/1"
    assert session_count() == 1


def test_no_assigned_session_today_has_helpful_manual_fallback(client, monkeypatch):
    monkeypatch.setattr(platform_v2, "datetime", FrozenClock)
    admin = setup_org(client)
    # Fridays are not considered Thursday sessions.
    response = post(
        client, "/platform/schedule", admin,
        track_id=1, staff_id=3, subject="مهني", weekday=4,
        starts_at="09:00", ends_at="11:00", reason="جدول يوم الجمعة",
    )
    assert response.status_code == 303
    signout(client, admin)
    signin(client, "trainer", "strong-test-password-12345")
    body = client.get("/platform/sessions/today").text
    assert "ما عندك حتى حصة مبرمجة اليوم" in body
    assert 'href="/platform/schedule"' in body
    assert session_count() == 0


def test_admins_and_unauthorized_users_cannot_use_staff_picker(client, monkeypatch):
    monkeypatch.setattr(platform_v2, "datetime", FrozenClock)
    admin = setup_org(client)
    assert client.get("/platform/sessions/today").status_code == 403
    signout(client, admin)
    signin(client, "coord1", "strong-test-password-12345")
    assert client.get("/platform/sessions/today").status_code == 403


def test_candidate_selection_does_not_guess_when_two_slots_current():
    from types import SimpleNamespace
    on = datetime(2026, 11, 12).date()
    slots = [
        SimpleNamespace(id=1, center_id=1, starts_at="08:30", ends_at="10:30",
                        weekday=3, specific_date=None, canceled=False),
        SimpleNamespace(id=2, center_id=2, starts_at="09:00", ends_at="11:00",
                        weekday=3, specific_date=None, canceled=False),
        SimpleNamespace(id=3, center_id=1, starts_at="10:30", ends_at="11:30",
                        weekday=3, specific_date=None, canceled=False),
        SimpleNamespace(id=4, center_id=1, starts_at="09:00", ends_at="10:00",
                        weekday=3, specific_date=None, canceled=True),
    ]
    selected = platform_v2.matching_staff_sessions(slots, on, "09:30", set())
    assert [slot.id for slot in selected] == [1, 2, 3]
    closed = platform_v2.matching_staff_sessions(slots, on, "09:30", {1})
    assert [slot.id for slot in closed] == [2]
