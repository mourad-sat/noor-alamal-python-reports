"""Regression checks for the scoped read-only pending-absence view."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app
from platform_v2_attendance_follow_up import PAGE_SIZE, pending_absences_query


def test_pending_absence_query_requires_unpermitted_absence():
    sql = str(pending_absences_query(7))
    assert "v2_attendance.status" in sql
    assert "v2_attendance.permit_id IS NULL" in sql
    assert "v2_enrollments.track_id" in sql
    assert "v2_sessions" in sql


def test_attendance_followup_route_is_registered():
    routes = [route for route in app.routes
              if getattr(route, "path", "") == "/platform/attendance/follow-up"]
    assert len(routes) == 1
    assert "GET" in routes[0].methods
    assert PAGE_SIZE == 100
