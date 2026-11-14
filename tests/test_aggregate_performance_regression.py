"""Performance regression: report aggregation remains role-scoped and accurate."""
from datetime import date

from sqlalchemy.orm import Session
from test_app import client


def test_approved_stats_scopes_by_slot_date_and_approval(client):
    from app import engine
    from platform_v2_aggregates import approved_stats
    from platform_v2_models import Attendance, SessionDay, SessionReport, Slot

    with Session(engine) as db:
        slots = [
            Slot(id=i, year_id=1, center_id=i, track_id=i, staff_id=1,
                 subject="test", kind="training", weekday=0,
                 starts_at="09:00", ends_at="10:00")
            for i in (1, 2)
        ]
        db.add_all(slots)
        db.add_all([
            SessionDay(id=1, slot_id=1, held_on=date(2026, 11, 10), status="held"),
            SessionDay(id=2, slot_id=2, held_on=date(2026, 11, 10), status="held"),
            SessionDay(id=3, slot_id=1, held_on=date(2026, 10, 10), status="held"),
            SessionDay(id=4, slot_id=1, held_on=date(2026, 11, 11), status="canceled"),
            SessionDay(id=5, slot_id=1, held_on=date(2026, 11, 12), status="held"),
            SessionDay(id=6, slot_id=1, held_on=date(2026, 11, 13), status="held"),
        ])
        db.add_all([
            SessionReport(id=i, session_id=i, author_id=1, subject="test",
                          status="draft" if i == 5 else "approved")
            for i in range(1, 7)
        ])
        db.add_all([
            Attendance(id=1, session_id=1, enrollment_id=1,
                       status="present", recorded_by="test"),
            Attendance(id=2, session_id=1, enrollment_id=2,
                       status="absent", recorded_by="test"),
            Attendance(id=3, session_id=2, enrollment_id=3,
                       status="absent", recorded_by="test"),
        ])
        db.commit()
        result = approved_stats(db, [slots[0]], date(2026, 11, 1), date(2026, 11, 30))
        assert result == dict(sessions=2, recorded=1, present=1, absent=1,
                              absent_days=1, missing=1)
        assert approved_stats(db, [], date(2026, 11, 1), date(2026, 11, 30)) == dict(
            sessions=0, recorded=0, present=0, absent=0, absent_days=0, missing=0)
