"""Test session report editor markup and existing data preservation."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from platform_v2 import session_report_editor


def report(**kwargs):
    defaults = dict(id=41, subject="تقرير سابق", objectives="هدف محفوظ",
                    activities="نشاط محفوظ", results="نتيجة محفوظة",
                    challenges="صعوبات سابقة", recommendations="اقتراح محفوظ",
                    review_comment="", status="draft")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_existing_draft_fields_stay_prefilled():
    markup = session_report_editor(12, "token", report(), "عنوان افتراضي")
    for value in ("تقرير سابق", "هدف محفوظ", "نشاط محفوظ",
                  "نتيجة محفوظة", "صعوبات سابقة", "اقتراح محفوظ"):
        assert value in markup
    assert 'action="/platform/sessions/12/report"' in markup
    assert 'action="/platform/reports/41/submit"' in markup
    assert 'name="_csrf"' in markup
    assert markup.count("<form") == 2
    assert "الحفظ لا يرسل التقرير" in markup


def test_unsaved_report_has_no_submit_form():
    markup = session_report_editor(12, "token", None, "موضوع الحصة")
    assert "موضوع الحصة" in markup
    assert 'action="/platform/reports/' not in markup
    assert "احفظ المسودة أولاً" in markup


def test_returned_comment_is_escaped_and_visible():
    markup = session_report_editor(12, "token", report(
        status="returned", review_comment="<script>unsafe</script>",
        activities="<b>not markup</b>",
    ), "عنوان")
    assert "أعيد التقرير للتصحيح" in markup
    assert "<script>unsafe</script>" not in markup
    assert "&lt;script&gt;" in markup
    assert "<b>not markup</b>" not in markup
    assert "&lt;b&gt;" in markup
