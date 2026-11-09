"""UI regressions for status-choice attendance without database writes."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # Initialize route registration before import.
from platform_v2 import attendance_choice_form, attendance_summary


def test_attendance_choice_preserves_csrf_and_explicit_save():
    html = attendance_choice_form(17, 23, "safe-token", None, False)
    assert 'action="/platform/sessions/17/attendance"' in html
    assert 'name="enrollment_id" value="23"' in html
    assert 'name="_csrf"' in html
    assert html.count('type="radio"') == 3
    assert html.count(" required") == 3
    assert "حفظ الحالة" in html
    assert " checked" not in html
    assert " disabled" not in html


def test_pending_absence_disables_present_and_late_only():
    html = attendance_choice_form(17, 23, "safe-token", "absent", True)
    assert html.count(" disabled") == 2
    assert html.count(" checked") == 1
    assert 'value="absent" required checked' in html
    assert "إذن المنسق" in html


def test_summary_distinguishes_unmarked_from_absent():
    enrollments = [
        SimpleNamespace(id=1), SimpleNamespace(id=2),
        SimpleNamespace(id=3), SimpleNamespace(id=4),
    ]
    marks = {
        1: SimpleNamespace(status="present"),
        2: SimpleNamespace(status="absent"),
        3: SimpleNamespace(status="late"),
    }
    html = attendance_summary(enrollments, marks)
    assert 'عدد المسجلين في الحصة: 4' in html
    for label in ("حاضر", "غائب", "متأخر", "لم يُسجّل بعد"):
        assert label in html
    assert html.count("<strong>1</strong>") == 4
    assert "ليسوا غائبين تلقائياً" in html


def test_overview_empty_session_has_zero_counts():
    html = attendance_summary([], {})
    assert "عدد المسجلين في الحصة: 0" in html
    assert html.count("<strong>0</strong>") == 4
