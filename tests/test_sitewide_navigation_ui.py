"""Sitewide portal navigation and layout regression tests."""
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
        "session": {"csrf": "test-token"},
    })


def test_shared_portal_has_breadcrumbs_and_navigation_search():
    user = {"role": "coordinator", "name": "منسق", "assignments": []}
    html = page(request_for("/platform/learners/17"), user, "ملف المستفيد", "").body.decode()
    assert '/static/platform_ui.js' in html
    assert 'data-nav-filter="pv2-desktop-links"' in html
    assert 'data-nav-filter="pv2-mobile-links"' in html
    assert '<nav class="pv2-breadcrumbs" aria-label="مسار الصفحة">' in html
    assert 'href="/platform"' in html
    assert 'aria-current="page"' in html
    assert 'id="main-content"' in html
    assert 'name="_csrf"' in html


def test_navigation_retains_scope_and_escapes_dynamic_text():
    user = {"role": "educator", "name": '<script>alert(1)</script>', "assignments": []}
    html = page(request_for("/platform"), user, "<bad>", "").body.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;bad&gt;" in html
    assert 'href="/platform/staff"' not in html
    assert 'href="/platform/profile-completeness"' not in html


def test_nav_script_uses_only_client_filtering_and_hides_nonmatches():
    path = Path(__file__).resolve().parents[1] / "static" / "platform_ui.js"
    source = path.read_text(encoding="utf-8")
    assert 'link.hidden = !visible' in source
    assert 'addEventListener("input"' in source
    assert 'fetch(' not in source
    assert 'localStorage' not in source
    assert 'sessionStorage' not in source
