"""Learner name dropdown for instructor grade entry, scoped to assessment tracks."""
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def test_grade_form_uses_named_active_learners_instead_of_manual_id(client):
    admin = setup_org(client)
    for track, name in ((1, "أمين لعيايدة"), (1, "ليلى لعيايدة"),
                        (2, "مستفيد من المركز الثاني")):
        assert post(client, "/platform/learners", admin,
                    full_name=name, year_id=1, track_id=track).status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/assessments", teacher,
                track_id=1, subject="مهني", title="اختبار المهارات",
                held_on="2026-11-06", coefficient="1").status_code == 303
    html = client.get("/platform/grades").text
    assert 'name="enrollment_id" data-grade-learner' in html
    assert 'name="assessment_id" data-grade-assessment' in html
    assert 'data-grade-learner-picker' in html
    assert 'value="1" data-track-id="1"' in html
    assert "أمين لعيايدة" in html
    assert "ليلى لعيايدة" in html
    assert "مستفيد من المركز الثاني" not in html
    assert 'name="enrollment_id" type="number"' not in html
    assert "رقم تسجيل المستفيد" not in html
    assert post(client, "/platform/grades", teacher,
                assessment_id=1, enrollment_id=1, score="17.5").status_code == 303
    assert "أمين لعيايدة" in client.get("/platform/grades").text
    assert post(client, "/platform/grades", teacher,
                assessment_id=1, enrollment_id=3, score="18").status_code == 403


def test_grade_form_without_assessments_prompts_creation(client):
    setup_org(client)
    signout(client, client.cookies.get("csrf", "unused"))
    teacher = signin(client, "trainer", PASSWORD)
    html = client.get("/platform/grades").text
    assert "أنشئ اختباراً أولاً" in html
    assert 'data-grade-learner-picker' not in html


def test_picker_script_filters_and_clears_stale_name():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "static/platform_ui.js").read_text()
    assert 'initializeGradeLearnerPicker()' in script
    assert 'option.getAttribute("data-track-id") === track' in script
    assert 'learner.value = ""' in script
