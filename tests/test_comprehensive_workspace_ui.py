"""Tests for report review UX and authorization-preserving presentation."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import platform_v2
from starlette.requests import Request
from platform_v2_models import SessionDay, Slot


def test_review_counts_are_scoped_to_supplied_reports():
    items = [
        SimpleNamespace(status="submitted"),
        SimpleNamespace(status="approved"),
        SimpleNamespace(status="submitted"),
        SimpleNamespace(status="draft"),
    ]
    html = platform_v2.report_status_summary(items)
    assert html.count("pv2-review-stat--") == 4
    assert "تنتظر المراجعة" in html
    assert '<strong>2</strong>' in html
    assert "أحدث التقارير المحمّلة فقط" in html


def test_report_filter_is_client_side_with_accessible_status():
    html = platform_v2.report_filter_box("example-table", "بحث")
    assert 'data-table-filter="example-table"' in html
    assert 'aria-live="polite"' in html
    assert "name=" not in html


def test_review_page_hides_return_and_delete_actions_by_role(monkeypatch):
    monkeypatch.setattr(platform_v2, "center_allowed", lambda user, center_id: center_id == 1)

    class DB:
        def scalars(self, query):
            return SimpleNamespace(all=lambda: [
                SimpleNamespace(id=51, status="submitted", session_id=9,
                                author_id=2, subject="عنوان تقرير", review_comment="")
            ])
        def get(self, model, item_id):
            if model is SessionDay:
                return SimpleNamespace(id=9, slot_id=7, held_on="2026-11-09")
            if model is Slot:
                return SimpleNamespace(id=7, subject="الرياضيات", center_id=1)
            raise AssertionError("Unexpected model")
    request = Request({
        "type": "http", "method": "GET", "path": "/platform/reports",
        "raw_path": b"/platform/reports", "query_string": b"",
        "headers": [], "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "test-token"},
    })
    teacher = {"role": "instructor", "id": 2, "name": "مكوّن", "assignments": []}
    html = platform_v2.review_reports(request, db=DB(), user=teacher).body.decode()
    assert "عنوان تقرير" in html
    assert 'data-table-filter="session-reports-table"' in html
    assert "خيارات حذف التقرير" not in html
    assert "تأكيد الإرجاع" not in html
    assert 'name="_csrf"' in html
    assert 'href="/platform/reports/51/attachments"' in html


def test_report_table_search_stays_local():
    source = (Path(__file__).resolve().parents[1] / "static" / "platform_ui.js").read_text(
        encoding="utf-8"
    )
    assert 'data-table-filter' in source
    assert 'row.hidden = !visible' in source
    assert 'fetch(' not in source
    assert 'localStorage' not in source
