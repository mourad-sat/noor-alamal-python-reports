"""The dark neon shell is shared without changing role-scoped navigation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from starlette.requests import Request
from platform_v2 import page


def request_for(path):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "example-token"},
    })


def test_neon_shell_on_key_operational_pages():
    user = {"role": "coordinator", "name": "المنسق", "assignments": []}
    for path in ("/platform/learners", "/platform/schedule", "/platform/reports",
                 "/platform/attendance/stats", "/platform/aggregates"):
        html = page(request_for(path), user, "تقرير", "").body.decode()
        assert 'class="pv2 pv2-neon-shell"' in html, path
        assert '/static/dashboard_theme.js' in html, path
        assert 'data-dashboard-theme-toggle' in html
        assert '<nav class="pv2-breadcrumbs"' in html
        assert '<h1 class="pv2-page-title">تقرير</h1>' in html
        assert 'name="_csrf"' in html
        assert 'data-nav-filter="pv2-mobile-links"' in html
        assert "المنسق" in html


def test_neon_shell_does_not_bypass_role_links():
    user = {"role": "educator", "name": "منشط", "assignments": []}
    html = page(request_for("/platform/learners"), user, "المستفيدون", "").body.decode()
    assert 'href="/platform/staff"' not in html
    assert 'href="/platform/audit"' not in html
    assert 'href="/platform/profile-completeness"' not in html
    assert 'href="/platform/learners"' in html


def test_theme_switcher_is_shared_and_search_remains_storage_free():
    root = Path(__file__).resolve().parents[1] / "static"
    theme = (root / "dashboard_theme.js").read_text(encoding="utf-8")
    search = (root / "platform_ui.js").read_text(encoding="utf-8")
    css = (root / "platform_ui.css").read_text(encoding="utf-8")
    assert 'document.querySelector(".pv2-neon-shell")' in theme
    assert "noor-dashboard-appearance" in theme
    assert "localStorage" not in search
    assert ".pv2-neon-shell .pv2-back-home" in css
    assert ".pv2-neon-shell .pv2-report-stat" in css
    assert "@media print" in css
