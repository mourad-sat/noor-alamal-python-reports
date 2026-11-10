"""Authorized attendance statistics: rates, scoping, audited CSV and to-do banner."""
from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_app import client, login  # noqa: F401
from test_platform_v2 import setup_org, signin, signout
from test_learner_track_cards import register

PASSWORD = "strong-test-password-12345"
RANGE = "start=2026-11-01&end=2026-11-09"


def seed(client):
    from app import engine
    from platform_v2_models import (
        Attendance, Enrollment, Learner, Permit, SessionDay, Slot,
    )
    admin = setup_org(client)
    a = register(client, admin, track_id=1, name="أمينة المتفوقة", source="S-001")
    b = register(client, admin, track_id=1, name="بلال المثابر", source="S-002")
    c = register(client, admin, track_id=1, name="ليلى بلا سجلات", source="S-003")
    register(client, admin, track_id=2, name="مستفيد مركز آخر", source="S-004")
    with Session(engine) as db:
        for en_id in (a, b, c):
            db.get(Enrollment, en_id).enrolled_on = date(2026, 11, 1)
        slot = Slot(
            year_id=1, center_id=1, track_id=1, staff_id=3,
            subject="مهني", kind="training", weekday=0,
            starts_at="09:00", ends_at="11:00",
        )
        db.add(slot)
        db.flush()
        days = {
            name: SessionDay(slot_id=slot.id, held_on=held_on, status=status)
            for name, held_on, status in [
                ("s1", date(2026, 11, 2), "open"),
                ("s2", date(2026, 11, 3), "open"),
                ("s3", date(2026, 11, 4), "open"),
                ("canceled", date(2026, 11, 5), "canceled"),
                ("old", date(2026, 10, 1), "open"),
                ("future", date.today() + timedelta(days=20), "open"),
            ]
        }
        db.add_all(days.values())
        permit = Permit(
            learner_id=db.get(Enrollment, b).learner_id,
            center_id=1, reason="موعد طبي", issued_by="test",
        )
        db.add(permit)
        db.flush()

        def mark(day, enrollment, status, with_permit=False):
            db.add(Attendance(
                session_id=days[day].id, enrollment_id=enrollment,
                status=status, permit_id=permit.id if with_permit else None,
                recorded_by="test",
            ))
        mark("s1", a, "present")
        mark("s2", a, "late")
        mark("s3", a, "absent")
        mark("s1", b, "absent", True)
        mark("s2", b, "absent")
        mark("s3", b, "present")
        mark("canceled", a, "absent")
        mark("old", a, "absent")
        db.commit()
    return admin, a, b, c


def test_stats_rates_and_excluded_sessions(client):
    from app import engine
    from platform_v2_attendance_stats import compute_track_stats, roster_rows
    _, a, b, c = seed(client)
    with Session(engine) as db:
        stats = compute_track_stats(db, 1, date(2026, 11, 1), date(2026, 11, 9))
        rows = {row["enrollment_id"]: row for row in roster_rows(db, 1, stats)}
    assert stats["sessions_held"] == 3
    assert (rows[a]["present"], rows[a]["late"], rows[a]["absent_open"], rows[a]["rate"]) == (1, 1, 1, 66.7)
    assert (rows[b]["absent_permit"], rows[b]["absent_open"], rows[b]["rate"]) == (1, 1, 33.3)
    assert (rows[c]["unrecorded"], rows[c]["rate"]) == (3, None)


def test_late_registration_not_counted_as_missing(client):
    from app import engine
    from platform_v2_models import Enrollment
    from platform_v2_attendance_stats import compute_track_stats, roster_rows
    _, _, _, c = seed(client)
    with Session(engine) as db:
        db.get(Enrollment, c).enrolled_on = date(2026, 11, 4)
        db.commit()
        stats = compute_track_stats(db, 1, date(2026, 11, 1), date(2026, 11, 9))
        rows = {row["enrollment_id"]: row for row in roster_rows(db, 1, stats)}
    assert rows[c]["unrecorded"] == 1


def test_page_filters_learner_names_by_track(client):
    seed(client)
    response = client.get(f"/platform/attendance/stats?track_id=1&{RANGE}")
    assert response.status_code == 200
    html = response.text
    assert "أمينة المتفوقة" in html and "ليلى بلا سجلات" in html
    assert "مستفيد مركز آخر" not in html
    assert 'data-table-filter="attendance-stats-table"' in html
    assert "66.7%" in html and "33.3%" in html
    assert "/platform/attendance/stats.csv?" in html
    picker = client.get("/platform/attendance/stats").text
    assert "أمينة المتفوقة" not in picker
    assert "track_id=1" in picker


def test_csv_audit_and_spreadsheet_safety(client):
    from app import engine
    from platform_v2_models import Audit, Learner, Enrollment
    admin, a, _, _ = seed(client)
    with Session(engine) as db:
        learner = db.get(Learner, db.get(Enrollment, a).learner_id)
        learner.source_code = "=2+2"
        db.commit()
    response = client.get(f"/platform/attendance/stats.csv?track_id=1&{RANGE}")
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("الاسم الكامل,الرمز الأصلي")
    assert "'=2+2" in text
    assert len(text.strip().splitlines()) == 4
    with Session(engine) as db:
        audit = db.scalar(select(Audit).where(
            Audit.action == "export", Audit.resource == "attendance_stats"
        ))
    assert audit is not None and audit.resource_id == 1


def test_cross_center_and_role_limits(client):
    admin, *_ = seed(client)
    signout(client, admin)
    signin(client, "coord2", PASSWORD)
    assert client.get(f"/platform/attendance/stats?track_id=1&{RANGE}").status_code == 403
    assert client.get(f"/platform/attendance/stats.csv?track_id=1&{RANGE}").status_code == 403
    client.cookies.clear()
    signin(client, "educator", PASSWORD)
    assert client.get(f"/platform/attendance/stats?track_id=1&{RANGE}").status_code == 200
    assert client.get(f"/platform/attendance/stats?track_id=2&{RANGE}").status_code == 403
    html = client.get(f"/platform/attendance/stats?track_id=1&{RANGE}").text
    assert "/platform/attendance/stats.csv" not in html
    assert client.get(f"/platform/attendance/stats.csv?track_id=1&{RANGE}").status_code == 403


def test_rejects_invalid_ranges(client):
    seed(client)
    assert client.get("/platform/attendance/stats?track_id=1&start=2026-11-09&end=2026-11-01").status_code == 422
    assert client.get("/platform/attendance/stats?track_id=1&start=2024-01-01&end=2026-11-01").status_code == 422


def test_dashboard_todo_shows_pending_approvals(client):
    from app import engine
    from platform_v2_models import SessionDay, SessionReport
    seed(client)
    assert "بانتظار المصادقة" not in client.get("/platform").text.split("مؤشرات العمل")[0]
    with Session(engine) as db:
        sid = db.scalar(select(SessionDay.id).where(
            SessionDay.held_on == date(2026, 11, 2)
        ))
        db.add(SessionReport(
            session_id=sid, author_id=3, subject="تقرير", status="submitted"
        ))
        db.commit()
    html = client.get("/platform").text
    assert "1 تقرير حصة بانتظار المصادقة" in html
