"""Fixed weekly timetable exceptions do not create learner absence."""
from test_app import client
from test_platform_v2 import setup_org, signin, signout, post

def test_coordinator_cancels_single_occurrence_then_adds_makeup(client):
    admin=setup_org(client)
    signout(client,admin)
    coord=signin(client,"coord1","strong-test-password-12345")
    created=post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
        subject="مهني",weekday=4,starts_at="14:00",ends_at="16:00",
        reason="برنامج حصص أسبوعي")
    assert created.status_code==303,created.text[:400]

    short_reason=post(client,"/platform/schedule/1/cancel-day",coord,
        held_on="2026-11-06",reason="لا")
    assert short_reason.status_code==422
    canceled=post(client,"/platform/schedule/1/cancel-day",coord,
        held_on="2026-11-06",reason="إلغاء الحصة لظرف استثنائي موثق")
    assert canceled.status_code==303,canceled.text[:400]
    repeated=post(client,"/platform/schedule/1/cancel-day",coord,
        held_on="2026-11-06",reason="إلغاء الحصة لظرف استثنائي موثق")
    assert repeated.status_code==409

    makeup=post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
        subject="مهني",weekday=4,starts_at="15:00",ends_at="16:30",
        specific_date="2026-11-06",reason="تعويض الحصة الملغاة لهذا الأسبوع")
    assert makeup.status_code==303,makeup.text[:500]

    signout(client,coord)
    trainer=signin(client,"trainer","strong-test-password-12345")
    canceled_entry=post(client,"/platform/sessions",trainer,slot_id=1,held_on="2026-11-06")
    assert canceled_entry.status_code==409
    makeup_entry=post(client,"/platform/sessions",trainer,slot_id=2,held_on="2026-11-06")
    assert makeup_entry.status_code==303
    subsequent=post(client,"/platform/sessions",trainer,slot_id=1,held_on="2026-11-13")
    assert subsequent.status_code==303
    assert post(client,"/platform/schedule/1/cancel-day",trainer,
        held_on="2026-11-13",reason="طلب الغاء مكتوب").status_code==403

def test_other_center_coordinator_cannot_cancel_session(client):
    admin=setup_org(client)
    signout(client,admin)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
        subject="مهني",weekday=4,starts_at="14:00",ends_at="16:00",
        reason="برنامج حصص أسبوعي").status_code==303
    signout(client,coord)
    other=signin(client,"coord2","strong-test-password-12345")
    refused=post(client,"/platform/schedule/1/cancel-day",other,
        held_on="2026-11-06",reason="الاعتذار عن الحصة لهذا اليوم")
    assert refused.status_code==403
