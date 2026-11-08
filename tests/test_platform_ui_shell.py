"""Regression tests for responsive Arabic platform shell."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.requests import Request
from platform_v2 import page


def request_for(path="/platform/learners"):
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "https",
        "server": ("testserver", 443),
        "session": {"csrf": "testcsrf"},
    })


def test_portal_shell_has_accessible_navigation_and_mobile_menu():
    user = {"role": "admin", "name": "الإدارة", "assignments": []}
    result = page(request_for(), user, "المستفيدون", "<section>محتوى</section>")
    html = result.body.decode("utf-8")
    assert 'lang="ar" dir="rtl"' in html
    assert '/static/platform_ui.css' in html
    assert 'class="pv2-sidebar"' in html
    assert 'class="pv2-mobile-menu"' in html
    assert 'aria-current="page"' in html
    assert 'href="#main-content"' in html
    assert 'name="_csrf"' in html
    assert "محتوى" in html


def test_portal_escapes_user_name_and_preserves_role_navigation():
    user = {"role": "coordinator", "name": "<script>alert(1)</script>", "assignments": []}
    html = page(request_for("/platform"), user, "لوحة القيادة", "").body.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "/platform/profile-completeness" in html
    assert 'href="/platform/staff"' not in html
