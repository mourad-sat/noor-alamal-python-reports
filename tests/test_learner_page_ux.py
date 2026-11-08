"""Basic learner-page UX regression checks with synthetic data."""
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # Initialize app before platform_v2 to avoid circular import.
import platform_v2
from starlette.requests import Request


class SelectorDB:
    def scalars(self, statement):
        return SimpleNamespace(all=lambda: [
            SimpleNamespace(id=7, center_id=1, year_id=2, name="مسلك تجريبي"),
        ])
    def get(self, model, key):
        return SimpleNamespace(label="2026/2027")


def test_selector_has_track_cards_without_learner_data(monkeypatch):
    monkeypatch.setattr(platform_v2, "ensure_data", lambda db: None)
    monkeypatch.setattr(platform_v2, "center_options", lambda db, user: [(1, "مركز تجريبي")])
    request = Request({
        "type": "http", "method": "GET", "path": "/platform/learners",
        "raw_path": b"/platform/learners", "query_string": b"",
        "headers": [], "scheme": "https", "server": ("testserver", 443),
        "session": {"csrf": "test"},
    })
    user = {"role": "admin", "name": "اختبار", "assignments": []}
    body = platform_v2.learners_page(request, track_id=None, db=SelectorDB(), user=user).body.decode()
    assert 'class="pv2-track-grid"' in body
    assert 'class="pv2-track-link"' in body
    assert 'href="/platform/learners?track_id=7"' in body
    assert "مسلك تجريبي" in body
    assert "لائحة المستفيدين" not in body


def test_edit_form_is_collapsed_by_default_in_template():
    source = Path(platform_v2.__file__).read_text(encoding="utf-8")
    assert '<details><summary>تسجيل مستفيد جديد' in source
    assert 'name="_csrf"' not in source or 'form("/platform/learners", csrf' in source
