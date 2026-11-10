"""Checks dashboard neon visualizations use actual scoped attendance only."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import platform_v2


def test_neon_chart_shows_real_recorded_rate_without_fabricated_data():
    data = {
        "present": 8, "late": 1, "absent": 1, "sessions": 3,
        "dates": [date(2026, 11, d) for d in range(5, 11)],
        "daily": [None, 80.0, 90.0, None, 100.0, None],
        "recent": [(date(2026, 11, 9), 2)],
    }
    trend, ring, rate = platform_v2.dashboard_neon_charts(data)
    assert rate == 90.0
    assert "--ring-present:80.00%" in ring
    assert "--ring-late:90.00%" in ring
    assert 'aria-label="حاضر: 8، متأخر: 1، غائب: 1"' in ring
    assert 'viewBox="0 0 520 206"' in trend
    assert trend.count("<circle ") == 3
    # Missing days are never connected as if observations existed.
    assert trend.count("<line x1=") == 6  # five grid lines, one contiguous segment


def test_no_attendance_does_not_show_false_zero_percent():
    data = {
        "present": 0, "late": 0, "absent": 0, "sessions": 0,
        "dates": [date(2026, 11, d) for d in range(5, 11)],
        "daily": [None] * 6, "recent": [],
    }
    trend, ring, rate = platform_v2.dashboard_neon_charts(data)
    assert rate is None
    assert "لا توجد سجلات حضور" in trend
    assert "نسبة الحضور" in ring
    assert "<strong>—</strong>" in ring


def test_snapshot_restricts_queries_to_authorized_center_ids():
    class DB:
        def __init__(self):
            self.statement = None
        def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(all=lambda: [
                (date(2026, 11, 9), "present", 4),
                (date(2026, 11, 9), "absent", 1),
            ])
        def scalar(self, statement):
            return 2
    db = DB()
    output = platform_v2.dashboard_attendance_snapshot(
        db, [17], date(2026, 11, 10)
    )
    assert output["present"] == 4
    assert output["absent"] == 1
    assert output["sessions"] == 2
    assert output["daily"][-2] == 80.0
    assert "v2_slots.center_id IN" in str(db.statement)
    assert output["recent"] == [(date(2026, 11, 9), 5)]


def test_dashboard_theme_is_scoped_to_home_only():
    source = Path(platform_v2.__file__).read_text(encoding="utf-8")
    assert 'if current_path == "/platform"' in source
    assert 'pv2-dashboard' in source
    assert 'data-dashboard-theme-toggle' in source
    js = (Path(__file__).resolve().parents[1] / "static" / "platform_ui.js").read_text()
    assert 'noor-dashboard-appearance' in js
