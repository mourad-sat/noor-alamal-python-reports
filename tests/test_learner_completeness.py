"""Regression tests for the read-only profile completeness checklist."""
from types import SimpleNamespace

from app import app
from platform_v2_learner_completeness import CHECKLIST, missing_profile_fields


def test_profile_checklist_detects_blank_and_missing_fields():
    learner = SimpleNamespace(
        born_on=None, birthplace=" ", phone="0600000000",
        address="", guardian_name="أحمد", guardian_phone=None,
    )
    assert missing_profile_fields(learner) == [
        "تاريخ الازدياد", "مكان الازدياد", "عنوان السكن", "هاتف ولي الأمر"
    ]


def test_profile_checklist_does_not_flag_populated_fields():
    values = {key: "موجود" for key, _ in CHECKLIST}
    learner = SimpleNamespace(**values)
    assert missing_profile_fields(learner) == []


def test_review_route_is_registered_once():
    routes = [route for route in app.routes
              if getattr(route, "path", "") == "/platform/profile-completeness"]
    assert len(routes) == 1
    assert "GET" in routes[0].methods
