"""Administrator-owned weekly schedule, conflict and history regression tests."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_app import client, login
from test_platform_v2 import post, setup_org, signin, signout


def values(**change):
    data = dict(track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="تعديل استعمال الزمن الأسبوعي")
    data.update(change)
    return data


def test_admin_creates_edits_and_audits_schedule(client):
    from app import engine
    from platform_v2_models import Slot, Audit
    csrf = setup_org(client)
    assert "إضافة حصة إلى استعمال الزمن الأسبوعي" in client.get("/platform/schedule").text
    assert post(client, "/platform/schedule", csrf, **values()).status_code == 303
    assert "حفظ تعديل استعمال الزمن" in client.get("/platform/schedule/1/edit").text
    assert post(client, "/platform/schedule/1/edit", "bad",
                **values(starts_at="09:00")).status_code == 403
    edited = post(client, "/platform/schedule/1/edit", csrf,
                  **values(starts_at="09:00", ends_at="11:00"))
    assert edited.status_code == 303, edited.text[:300]
    with Session(engine) as db:
        slots = db.scalars(select(Slot)).all()
        assert len(slots) == 1
        assert (slots[0].starts_at, slots[0].ends_at) == ("09:00", "11:00")
        assert db.scalar(select(Audit).where(
            Audit.action == "schedule_edit", Audit.resource_id == 1
        )) is not None


def test_admin_cannot_assign_unassigned_staff_or_create_overlaps(client):
    csrf = setup_org(client)
    assert post(client, "/platform/schedule", csrf, **values()).status_code == 303
    clash = post(client, "/platform/schedule", csrf,
                 **values(track_id=2, starts_at="09:00", ends_at="11:00"))
    assert clash.status_code == 409
    different_center = post(client, "/platform/schedule/1/edit", csrf,
                            **values(track_id=2, staff_id=4, subject="الرياضيات", weekday=2,
                                     starts_at="12:00", ends_at="13:00"))
    assert different_center.status_code == 403


def test_historical_daily_sessions_are_not_modified_on_timetable_change(client):
    from app import engine
    from platform_v2_models import Slot, SessionDay, Audit
    csrf = setup_org(client)
    assert post(client, "/platform/schedule", csrf, **values()).status_code == 303
    assert post(client, "/platform/sessions", csrf,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert "سيُحفَظ توقيتها السابق للأرشيف" in client.get(
        "/platform/schedule/1/edit").text
    edited = post(client, "/platform/schedule/1/edit", csrf,
                  **values(starts_at="10:00", ends_at="12:00"))
    assert edited.status_code == 303, edited.text[:300]
    with Session(engine) as db:
        old, current = db.get(Slot, 1), db.get(Slot, 2)
        assert old.canceled is True and old.starts_at == "08:00"
        assert current.canceled is False and current.starts_at == "10:00"
        assert db.get(SessionDay, 1).slot_id == old.id
        assert db.scalar(select(Audit).where(
            Audit.action == "schedule_supersede", Audit.resource_id == old.id
        )) is not None


def test_coordinator_only_owns_own_center_instructor_cannot_edit(client):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin, **values()).status_code == 303
    signout(client, admin)
    coord = signin(client, "coord2", "strong-test-password-12345")
    assert client.get("/platform/schedule/1/edit").status_code == 403
    assert post(client, "/platform/schedule/1/edit", coord, **values()).status_code == 403
    assert post(client, "/platform/schedule", coord, **values()).status_code == 403
    signout(client, coord)
    signin(client, "trainer", "strong-test-password-12345")
    assert client.get("/platform/schedule/1/edit").status_code == 403


def test_admin_can_cancel_occurrence_without_canceling_weekly_slot(client):
    from app import engine
    from platform_v2_models import Slot, SessionDay
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin, **values()).status_code == 303
    canceled = post(client, "/platform/schedule/1/cancel-day", admin,
                    held_on="2026-11-06", reason="توقف استثنائي عن التكوين")
    assert canceled.status_code == 303, canceled.text[:300]
    with Session(engine) as db:
        assert not db.get(Slot, 1).canceled
        day = db.scalar(select(SessionDay).where(SessionDay.slot_id == 1))
        assert day.status == "canceled"
