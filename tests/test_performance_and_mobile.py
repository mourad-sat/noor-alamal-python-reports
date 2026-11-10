"""Regression tests for fast learner rosters and mobile display."""
from datetime import date, timedelta
from sqlalchemy import event

from test_app import client, login  # noqa: F401
from test_platform_v2 import setup_org, post
from test_learner_track_cards import register


class QueryCounter:
    def __init__(self):
        self.count = 0
        self.engine = None

    def __enter__(self):
        from app import engine
        self.engine = engine
        event.listen(engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)

    def _hit(self, *args, **kwargs):
        self.count += 1


def roster_query_count(client, track_id=1):
    with QueryCounter() as counter:
        response = client.get(f"/platform/learners?track_id={track_id}")
    assert response.status_code == 200, response.text[:300]
    return counter.count, response.text


def test_roster_query_count_does_not_grow_with_learners(client):
    admin = setup_org(client)
    register(client, admin, track_id=1, name="مستفيد 0", source="C-000")
    baseline, _ = roster_query_count(client)
    for index in range(1, 13):
        register(client, admin, track_id=1, name=f"مستفيد {index}", source=f"C-{index:03d}")
    with_many, html = roster_query_count(client)
    assert html.count('data-label="الاسم الكامل"') == 13
    assert with_many == baseline, (baseline, with_many)


def test_roster_is_searchable_and_card_ready_on_mobile(client):
    admin = setup_org(client)
    register(client, admin, track_id=1, name="سلمى الإدريسي", source="D-001")
    _, html = roster_query_count(client)
    assert 'data-table-filter="learners-roster-table"' in html
    assert 'id="learners-roster-table"' in html
    assert 'class="table-scroll table-cards"' in html
    css = client.get("/static/platform_ui.css").text
    assert ".table-cards tbody tr[hidden]{display:none}" in css
    assert "font-size:16px" in css


def test_pending_absence_and_exclusion_flags_still_shown(client):
    from sqlalchemy.orm import Session
    from app import engine
    from platform_v2_models import Discipline

    admin = setup_org(client)
    clean = register(client, admin, track_id=1, name="بدون ملاحظات", source="E-001")
    excluded = register(client, admin, track_id=1, name="مقصى مؤقتا", source="E-002")
    with Session(engine) as db:
        db.add(Discipline(
            enrollment_id=excluded, decision="x", minutes="x", status="effective",
            excluded_from=date.today() - timedelta(days=1),
            excluded_through=date.today() + timedelta(days=3)
        ))
        db.commit()
    _, html = roster_query_count(client)
    assert html.count("إقصاء تأديبي") == 1
    assert html.count("لا غياب معلق") == 2
    assert clean != excluded
