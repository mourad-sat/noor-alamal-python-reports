"""Reference #4 visual refinement without replacing secured portal workflows."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from starlette.requests import Request
from platform_v2 import page, neon_icon


def page_request(path):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [], "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "token"},
    })


def test_neon_svg_icons_exist_and_use_fixed_names():
    root = Path(__file__).resolve().parents[1] / "static"
    path = root / "neon_icons.svg"
    svg = ET.parse(path).getroot()
    ids = {item.attrib["id"] for item in svg.iter()
           if item.tag.endswith("symbol") and "id" in item.attrib}
    for name in ("home", "users", "user-plus", "calendar", "chart",
                 "documents", "bell", "settings", "check"):
        assert name in ids
        assert f'href="/static/neon_icons.svg#{name}"' in neon_icon(name)
    assert "<script>" not in neon_icon("<script>")
    assert "neon_icons.svg#folder" in neon_icon("<script>")


def test_header_and_navigation_are_role_safe():
    user = {"role": "educator", "name": "منشط", "assignments": []}
    for path in ("/platform", "/platform/learners", "/platform/reports"):
        body = page(page_request(path), user, "تجربة", "").body.decode("utf-8")
        assert "neon_icons.svg#home" in body
        assert "neon_icons.svg#bell" in body
        assert 'aria-label="فتح التنبيهات"' in body
        assert 'name="_csrf"' in body
        assert 'href="/platform/staff"' not in body
        assert 'href="/platform/audit"' not in body
        assert 'href="/platform/notifications"' in body


def test_quick_search_shortcut_stays_local_and_accessible():
    js = (Path(__file__).resolve().parents[1] / "static" /
          "platform_ui.js").read_text(encoding="utf-8")
    assert 'event.ctrlKey || event.metaKey' in js
    assert 'event.preventDefault()' in js
    assert 'panel.open = true' in js
    assert "localStorage" not in js
    assert "fetch(" not in js


def test_responsive_and_print_styles_are_present():
    css = (Path(__file__).resolve().parents[1] / "static" /
           "platform_ui.css").read_text(encoding="utf-8")
    assert ".pv2-neon-shell .pv2-icon" in css
    assert ".pv2-neon-shell .pv2-header-notifications" in css
    assert "@media(max-width:380px)" in css
    assert ".pv2-neon-shell .pv2-neon-metric" in css
    assert "@media print" in css
