"""Dashboard presentation regression tests without touching production data."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.requests import Request
import app  # Load the full application before importing its route module.
import platform_v2


class FakeDB:
    def __init__(self):
        self.scalar_calls = 0
        self.scalars_calls = 0

    def scalars(self, statement):
        self.scalars_calls += 1
        if self.scalars_calls == 1:
            return SimpleNamespace(all=lambda: [
                SimpleNamespace(id=17, center_id=1),
                SimpleNamespace(id=18, center_id=1),
            ])
        if self.scalars_calls == 2:
            return SimpleNamespace(all=lambda: [
                SimpleNamespace(id=1, name="مركز تجريبي", area="حي تجريبي"),
            ])
        return SimpleNamespace(all=lambda: [
            SimpleNamespace(id=4, center_id=1),
        ])

    def execute(self, statement):
        return SimpleNamespace(all=lambda: [])

    def scalar(self, statement):
        self.scalar_calls += 1
        return 3 if self.scalar_calls == 1 else 1


def test_dashboard_has_metrics_shortcuts_and_center_overview(monkeypatch):
    monkeypatch.setattr(platform_v2, "ensure_data", lambda db: None)
    monkeypatch.setattr(platform_v2, "center_options", lambda db, user: [(1, "مركز تجريبي")])
    request = Request({
        "type": "http", "method": "GET", "path": "/platform",
        "raw_path": b"/platform", "query_string": b"", "headers": [],
        "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "test"},
    })
    user = {"role": "coordinator", "name": "اختبار", "assignments": []}
    html = platform_v2.home(request, db=FakeDB(), user=user).body.decode()
    assert 'class="pv2 pv2-dashboard"' in html
    assert 'class="pv2-neon-metrics"' in html
    assert 'class="pv2-neon-charts"' in html
    assert 'class="pv2-neon-ring pv2-neon-ring--empty"' in html
    assert 'class="pv2-neon-actions"' in html
    assert 'data-dashboard-theme-toggle' in html
    assert "حصص الغياب المعلقة" in html
    assert "المراكز المتاحة" in html
    assert "مركز تجريبي" in html
    assert 'href="/platform/profile-completeness"' in html
    assert 'href="/platform/staff"' not in html
