"""Independent two-week morning/evening timetable, printing, opening and permissions."""
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import select
from test_app import client
from test_platform_v2 import setup_org, post, signin, signout

PASSWORD = "strong-test-password-12345"


def config(client, csrf, center, week, shift):
    return post(client, "/platform/schedule/rotation", csrf,
                center_id=center, anchor=week,
                anchor_shift=shift, reason="تطبيق التناوب الأسبوعي للمركز")


def slot(client, csrf, track, start, end, shift="both", weekday=0):
    return post(client, "/platform/schedule", csrf, track_id=track,
                staff_id=3, subject="مهني", weekday=weekday,
                starts_at=start, ends_at=end, shift=shift,
                reason="توقيت مختلف بين الأسبوعين")


def test_independent_monday_anchors_and_rotation(client):
    from platform_shift_rotation import week_shift, monday, slot_shift, encode_slot_kind
    from app import engine
    admin = setup_org(client)
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert config(client, admin, 2, "2026-11-11", "evening").status_code == 303
    assert monday(date(2026, 11, 11)) == date(2026, 11, 9)
    with Session(engine) as db:
        assert week_shift(db, 1, date(2026, 11, 9)) == "morning"
        assert week_shift(db, 1, date(2026, 11, 16)) == "evening"
        assert week_shift(db, 1, date(2026, 11, 23)) == "morning"
        assert week_shift(db, 1, date(2026, 11, 2)) == "evening"
        assert week_shift(db, 2, date(2026, 11, 9)) == "evening"
        assert week_shift(db, 2, date(2026, 11, 16)) == "morning"
    assert encode_slot_kind("مهني", "morning") == "professional_am"
    assert encode_slot_kind("الرياضيات", "evening") == "pedagogical_pm"


def test_center_weekly_grid_filters_by_selected_shift_and_keeps_legacy(client):
    admin = setup_org(client)
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert slot(client, admin, 1, "08:00", "10:00", "morning").status_code == 303
    assert slot(client, admin, 1, "14:00", "16:00", "evening").status_code == 303
    assert slot(client, admin, 1, "11:00", "12:00").status_code == 303
    morning = client.get("/platform/schedule?center_id=1&week_of=2026-11-09")
    evening = client.get("/platform/schedule?center_id=1&week_of=2026-11-16")
    first = morning.text.split('class="pv2-weekly-grid">', 1)[1].split("</section>", 1)[0]
    second = evening.text.split('class="pv2-weekly-grid">', 1)[1].split("</section>", 1)[0]
    assert "08:00–10:00" in first
    assert "14:00–16:00" not in first
    assert "14:00–16:00" in second
    assert "08:00–10:00" not in second
    assert "11:00–12:00" in first and "11:00–12:00" in second
    assert "أسبوع صباحي" in morning.text and "أسبوع مسائي" in evening.text
    assert 'name="week_of"' in evening.text
    assert 'name="anchor_shift"' in morning.text


def test_same_times_different_weeks_do_not_conflict(client):
    admin = setup_org(client)
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert slot(client, admin, 1, "08:00", "10:00", "morning").status_code == 303
    assert slot(client, admin, 1, "08:00", "10:00", "evening").status_code == 303
    assert slot(client, admin, 1, "08:30", "09:30", "morning").status_code == 409


def test_session_opening_rejects_wrong_week_and_preserves_history(client):
    admin = setup_org(client)
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert slot(client, admin, 1, "08:00", "10:00", "morning").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    bad = post(client, "/platform/sessions", teacher,
               slot_id=1, held_on="2026-11-16")
    assert bad.status_code == 409
    good = post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-09")
    assert good.status_code == 303
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-09").status_code == 303
    assert client.get("/platform/sessions/1").status_code == 200


def test_center_and_staff_prints_use_requested_week(client):
    admin = setup_org(client)
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert config(client, admin, 2, "2026-11-09", "evening").status_code == 303
    assert slot(client, admin, 1, "08:00", "10:00", "morning").status_code == 303
    assert slot(client, admin, 1, "14:00", "16:00", "evening").status_code == 303
    assert slot(client, admin, 2, "16:00", "18:00", "evening").status_code == 303
    one = client.get("/platform/schedule/print?mode=center&center_id=1&week_of=2026-11-09")
    both = client.get("/platform/schedule/print?mode=staff&staff_id=3&week_of=2026-11-09")
    swapped = client.get("/platform/schedule/print?mode=staff&staff_id=3&week_of=2026-11-16")
    assert one.status_code == both.status_code == swapped.status_code == 200
    assert "08:00–10:00" in one.text and "14:00–16:00" not in one.text
    assert "08:00–10:00" in both.text and "16:00–18:00" in both.text
    assert "14:00–16:00" not in both.text
    assert "14:00–16:00" in swapped.text and "16:00–18:00" not in swapped.text
    assert "أسبوع صباحي" in both.text and "أسبوع مسائي" in both.text
    assert 'name="week_of"' in both.text


def test_coordinator_only_manages_own_center_rotation(client):
    admin = setup_org(client)
    signout(client, admin)
    c1 = signin(client, "coord1", PASSWORD)
    assert config(client, c1, 1, "2026-11-09", "morning").status_code == 303
    assert config(client, c1, 2, "2026-11-09", "morning").status_code == 403
    assert config(client, c1, 1, "2026-11-09", "invalid").status_code == 422
    signout(client, c1)
    teacher = signin(client, "trainer", PASSWORD)
    assert config(client, teacher, 1, "2026-11-09", "evening").status_code == 403
    html = client.get("/platform/schedule?center_id=1").text
    assert 'action="/platform/schedule/rotation"' not in html


def test_existing_legacy_slots_are_not_reclassified_automatically(client):
    admin = setup_org(client)
    assert slot(client, admin, 1, "09:00", "11:00").status_code == 303
    assert config(client, admin, 1, "2026-11-09", "morning").status_code == 303
    assert "09:00–11:00" in client.get(
        "/platform/schedule?center_id=1&week_of=2026-11-16").text
