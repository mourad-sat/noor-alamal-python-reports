"""Proposed staff timetable changes require authorized coordinator/admin approval."""
from sqlalchemy.orm import Session
from sqlalchemy import select
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"
PROPOSE = "/platform/schedule/proposals"


def payload(track=1, slot_id=None, **updates):
    data = dict(track_id=track, subject="مهني", weekday=4, starts_at="08:00",
                ends_at="10:00", shift="both",
                reason="إضافة حصة حسب استعمال الزمن الحقيقي")
    if slot_id:
        data["slot_id"] = slot_id
    data.update(updates)
    return data


def proposals():
    from app import engine
    from platform_v2_models import ScheduleProposal
    with Session(engine) as db:
        return [(r.id, r.center_id, r.status, r.slot_id)
                for r in db.scalars(select(ScheduleProposal).order_by(ScheduleProposal.id))]


def slots():
    from app import engine
    from platform_v2_models import Slot
    with Session(engine) as db:
        return [(r.id, r.center_id, r.starts_at, r.ends_at, r.canceled)
                for r in db.scalars(select(Slot).order_by(Slot.id))]


def test_staff_proposes_both_centers_then_own_coordinator_and_admin_approve(client):
    admin = setup_org(client)
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    home = client.get("/platform").text
    assert 'href="/platform/schedule/proposals"' in home
    schedule = client.get("/platform/schedule?center_id=1").text
    assert "إضافة حصة أو متابعة طلباتي" in schedule
    assert post(client, "/platform/schedule", teacher, staff_id=3, **payload()).status_code == 403

    first = post(client, PROPOSE, teacher, **payload())
    second = post(client, PROPOSE, teacher,
                  **payload(track=2, starts_at="11:00", ends_at="13:00"))
    assert first.status_code == second.status_code == 303
    assert len(slots()) == 0, "Unapproved staff proposals must not create live slots"
    assert proposals() == [(1, 1, "pending", None), (2, 2, "pending", None)]
    visible = client.get(PROPOSE).text
    assert "بانتظار المصادقة" in visible
    signout(client, teacher)

    coord1 = signin(client, "coord1", PASSWORD)
    page = client.get(PROPOSE).text
    assert "منسق" not in page or "طلبات اعتماد" in page
    assert "مركز حمزة عبد المطلب" not in page
    assert post(client, f"{PROPOSE}/2/review", coord1,
                decision="approved").status_code == 403
    assert post(client, f"{PROPOSE}/1/review", coord1,
                decision="approved").status_code == 303
    assert slots() == [(1, 1, "08:00", "10:00", False)]
    signout(client, coord1)

    admin = signin(client, "admin", "correct-password")
    assert "مركز حمزة عبد المطلب" in client.get(PROPOSE).text
    assert post(client, f"{PROPOSE}/2/review", admin,
                decision="approved").status_code == 303
    assert len(slots()) == 2
    assert proposals() == [(1, 1, "approved", None), (2, 2, "approved", None)]
    assert post(client, f"{PROPOSE}/1/review", admin,
                decision="approved").status_code == 409


def test_rejection_preserves_live_timetable_and_notifies_author(client):
    admin = setup_org(client)
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, PROPOSE, teacher, **payload()).status_code == 303
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    assert post(client, f"{PROPOSE}/1/review", coord,
                decision="rejected", comment="التوقيت غير متوافق").status_code == 303
    assert slots() == []
    signout(client, coord)
    teacher = signin(client, "trainer", PASSWORD)
    page = client.get(PROPOSE).text
    assert "مرفوض" in page
    assert "التوقيت غير متوافق" in page
    assert "غير متوافق" in client.get("/platform/notifications").text


def test_staff_proposes_edit_and_approved_revision_preserves_old_session(client):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00", shift="both",
                reason="البرمجة الحالية").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher,
                slot_id=1, held_on="2026-11-06").status_code == 303
    assert 'href="/platform/schedule/1/propose"' in client.get(
        "/platform/schedule?center_id=1").text
    form = client.get("/platform/schedule/1/propose")
    assert form.status_code == 200 and 'name="slot_id" value="1"' in form.text
    new = post(client, PROPOSE, teacher,
               **payload(slot_id=1, starts_at="14:00", ends_at="16:00"))
    assert new.status_code == 303
    assert slots() == [(1, 1, "08:00", "10:00", False)]
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    assert post(client, f"{PROPOSE}/1/review", coord,
                decision="approved").status_code == 303
    assert slots() == [
        (1, 1, "08:00", "10:00", True),
        (2, 1, "14:00", "16:00", False),
    ]
    signout(client, coord)
    teacher = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/sessions/1").status_code == 200


def test_stale_edits_cannot_overwrite_coordinator_changes(client):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="الجدول الحالي").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, PROPOSE, teacher,
                **payload(slot_id=1, starts_at="11:00", ends_at="13:00")).status_code == 303
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    changed = post(client, "/platform/schedule/1/edit", coord,
                   track_id=1, staff_id=3, subject="مهني", weekday=4,
                   starts_at="09:00", ends_at="11:00",
                   reason="تصحيح الجدول الحالي")
    assert changed.status_code == 303
    stale = post(client, f"{PROPOSE}/1/review", coord,
                 decision="approved")
    assert stale.status_code == 409
    assert slots() == [(1, 1, "09:00", "11:00", False)]
    assert proposals() == [(1, 1, "pending", 1)]


def test_approval_conflicts_are_atomic_and_pending(client):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin,
                track_id=1, staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="حصة معتمدة").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, PROPOSE, teacher,
                **payload(starts_at="09:00", ends_at="11:00")).status_code == 303
    signout(client, teacher)
    admin = signin(client, "admin", "correct-password")
    conflict = post(client, f"{PROPOSE}/1/review", admin,
                    decision="approved")
    assert conflict.status_code == 409
    assert proposals() == [(1, 1, "pending", None)]
    assert slots() == [(1, 1, "08:00", "10:00", False)]


def test_invalid_assignment_and_role_limits(client):
    admin = setup_org(client)
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, PROPOSE, teacher,
                **payload(track=1, subject="الرياضيات")).status_code == 403
    assert post(client, PROPOSE, teacher,
                **payload(track=999)).status_code == 403
    assert post(client, PROPOSE, teacher,
                **payload(track=1, shift="invalid")).status_code == 422
    assert post(client, PROPOSE, teacher,
                **payload(track=1, weekday=3,
                          specific_date="2026-11-06")).status_code == 422
    assert not proposals()
    signout(client, teacher)
    coordinator = signin(client, "coord1", PASSWORD)
    assert post(client, PROPOSE, coordinator, **payload()).status_code == 403


def test_admin_has_coordinator_report_permit_notice_and_schedule_permissions(client):
    admin = setup_org(client)
    create = post(client, "/platform/schedule", admin,
                  track_id=1, staff_id=3, subject="مهني", weekday=4,
                  starts_at="08:00", ends_at="10:00",
                  reason="جدولة الحصص الرسمية")
    assert create.status_code == 303
    # Admin sees center-level approval forms, not just read-only report rows.
    # These same server routes are protected by coordinator() shared helper.
    from platform_v2 import coordinator
    coordinator({"role": "admin", "assignments": []}, 1)
    from fastapi import HTTPException
    try:
        coordinator({"role": "instructor", "assignments": []}, 1)
        assert False, "an instructor should not inherit approvals"
    except HTTPException as err:
        assert err.status_code == 403
