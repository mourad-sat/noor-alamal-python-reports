"""Regression tests for weekly/monthly report presentation and scoped export."""
from pathlib import Path
from types import SimpleNamespace
from datetime import date
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # Initialize registered route modules first.
import platform_v2_aggregates as reports


def sample_stats():
    return dict(sessions=5, recorded=4, present=12, absent=3, absent_days=2, missing=1)


def test_period_bounds_and_chart_accessibility():
    assert reports.period_bounds("weekly", date(2026, 11, 9)) == (
        date(2026, 11, 9), date(2026, 11, 15)
    )
    assert reports.period_bounds("monthly", date(2026, 12, 9)) == (
        date(2026, 12, 1), date(2026, 12, 31)
    )
    markup = reports.attendance_visual(sample_stats())
    assert 'role="img"' in markup
    assert "80" in markup
    assert "حضور وتأخر: 12" in markup
    assert "حصص دون سجل حضور" in reports.stats_html(sample_stats())


def test_empty_chart_avoids_inventing_a_percent():
    data = sample_stats()
    data["present"] = data["absent"] = 0
    assert "لا توجد سجلات" in reports.attendance_visual(data)


class FakeDB:
    def __init__(self):
        self.calls = 0

    def scalars(self, query):
        self.calls += 1
        if self.calls == 1:
            rows = [SimpleNamespace(id=11, active=True, label="2026/2027", starts_on=date(2026, 9, 1))]
        elif self.calls == 2:
            rows = [
                SimpleNamespace(id=4, year_id=11, center_id=1, name="مسلك مسموح"),
                SimpleNamespace(id=5, year_id=11, center_id=2, name="مسلك محجوب"),
            ]
        elif self.calls == 3:
            rows = [
                SimpleNamespace(id=7, year_id=11, center_id=1, track_id=4, canceled=False),
                SimpleNamespace(id=8, year_id=11, center_id=2, track_id=5, canceled=False),
            ]
        else:
            rows = [
                SimpleNamespace(id=1, name="مركز مسموح"),
                SimpleNamespace(id=2, name="مركز محجوب"),
            ]
        return SimpleNamespace(all=lambda: rows)


def test_report_scope_filters_other_centers(monkeypatch):
    monkeypatch.setattr(reports, "center_allowed", lambda user, center_id: center_id == 1)
    user = {"role": "coordinator", "assignments": []}
    _, _, _, _, year, accessible = reports.report_context(
        FakeDB(), user, "monthly", date(2026, 11, 9), None
    )
    assert year == 11
    assert len(accessible) == 1
    assert accessible[0][0].id == 1
    assert [t.id for t in accessible[0][1]] == [4]
    assert [slot.id for slot in accessible[0][2]] == [7]


def test_csv_exports_aggregate_values_only(monkeypatch):
    center = SimpleNamespace(id=1, name="مركز تجريبي")
    track = SimpleNamespace(id=4, name="مسلك تجريبي")
    slot = SimpleNamespace(id=7, track_id=4)
    monkeypatch.setattr(reports, "report_context", lambda *args: (
        date(2026, 11, 9), date(2026, 11, 9), date(2026, 11, 15),
        [], 11, [(center, [track], [slot])]
    ))
    monkeypatch.setattr(reports, "approved_stats", lambda *args: sample_stats())
    response = reports.aggregate_csv(
        period="weekly", anchor=date(2026, 11, 9), year_id=11,
        db=None, user={"role": "coordinator", "assignments": []},
    )
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])
    data = asyncio.run(collect()).decode("utf-8-sig")
    assert "إجمالي المركز" in data
    assert "مسلك تجريبي" in data
    assert "إجمالي الجمعية" not in data
    assert "اسم المستفيد" not in data
    assert "attachment" in response.headers["content-disposition"]
