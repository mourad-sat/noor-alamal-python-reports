"""Visual-regression contracts for six dashboard discrepancies reported in a live screenshot."""
from pathlib import Path
import sys
from types import SimpleNamespace
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import platform_v2
from starlette.requests import Request


def page_request(path):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": [],
        "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "example"},
    })


def test_grouped_navigation_shows_only_authorized_links():
    teacher = {"role": "educator", "name": "منشط", "assignments": []}
    html = platform_v2.page(page_request("/platform/learners"), teacher, "المستفيدون", "").body.decode()
    assert html.count('data-nav-group') >= 6  # desktop and mobile sections
    assert "المستفيدون والتكوين" in html
    assert "الحضور والتقارير" in html
    assert "الإدارة والخدمات" in html
    assert 'href="/platform/staff"' not in html
    assert 'href="/platform/audit"' not in html
    assert 'href="/platform/learners"' in html
    assert 'data-nav-filter="pv2-mobile-links"' in html


def test_navigation_filter_hides_group_headings_without_matches():
    source = (Path(__file__).resolve().parents[1] / "static" /
              "platform_ui.js").read_text(encoding="utf-8")
    assert 'root.querySelectorAll("[data-nav-group]")' in source
    assert 'group.hidden = !groupLinks.some' in source
    assert "fetch(" not in source
    assert "localStorage" not in source


def test_empty_attendance_chart_retains_nonfabricated_state():
    six_days = [date(2026, 11, day) for day in range(5, 11)]
    snapshot = {
        "present": 0, "late": 0, "absent": 0, "sessions": 0,
        "dates": six_days, "daily": [None] * 6, "recent": [],
    }
    trend, ring, rate = platform_v2.dashboard_neon_charts(snapshot)
    assert rate is None
    assert "لا توجد سجلات حضور" in trend
    assert "pv2-neon-chart-blank" in trend
    assert "ستظهر نسبة الحضور هنا بعد تسجيل الحصص" in trend
    assert 'class="pv2-neon-ring pv2-neon-ring--empty"' in ring
    assert "<strong>—</strong>" in ring


def test_kpi_markup_has_a_separate_text_column(monkeypatch):
    from test_dashboard_experience import FakeDB
    monkeypatch.setattr(platform_v2, "ensure_data", lambda db: None)
    monkeypatch.setattr(platform_v2, "center_options", lambda db, user: [(1, "مركز")])
    html = platform_v2.home(
        page_request("/platform"), FakeDB(),
        {"role": "coordinator", "name": "منسق", "assignments": []},
    ).body.decode()
    assert html.count('class="pv2-neon-metric-text"') == 4
    assert html.count('class="pv2-neon-metric-icon"') == 4
    assert 'class="pv2-neon-charts"' in html
    assert "pv2-neon-chart-blank" in html


def test_full_width_and_mobile_responsive_styles():
    css = (Path(__file__).resolve().parents[1] / "static" /
           "platform_ui.css").read_text(encoding="utf-8")
    assert ".pv2-neon-shell .pv2-main{" in css
    assert "width:100%;max-width:none;" in css
    assert "grid-template-columns:clamp(33px,3.2vw,51px) minmax(0,1fr)" in css
    assert ".pv2-neon-shell .pv2-nav-group[hidden]{display:none!important}" in css
    assert "@media(max-width:700px)" in css
    assert ".pv2-neon-shell .pv2-neon-chart-blank" in css
