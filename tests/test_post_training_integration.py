"""Post-training follow-ups: scoped privacy, append-only history, accurate cohorts."""
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def setup_learners(client):
    admin = setup_org(client)
    for name, track in (("مستفيد الإدماج لعيايدة", 1), ("مستفيد الإدماج بطانة", 2)):
        result = post(client, "/platform/learners", admin,
                      full_name=name, year_id=1, track_id=track)
        assert result.status_code == 303, result.text[:400]
    return admin


def followup(client, csrf, learner_id, **overrides):
    data = dict(
        outcome="needs_support",
        observed_on="2026-11-06",
        organization="",
        pathway_details="يحتاج إلى التوجيه",
        intervention="مقابلة تربوية",
        next_followup_on="2026-11-20",
    )
    data.update(overrides)
    return post(client, f"/platform/learners/{learner_id}/integration",
                csrf, **data)


def test_history_print_and_latest_summary_without_changing_existing_records(client):
    admin = setup_learners(client)
    assert "فتح بطاقة تتبع الإدماج" in client.get("/platform/learners/1").text
    assert client.get("/platform/integration").status_code == 200
    assert "مستفيد الإدماج لعيايدة" not in client.get("/platform/integration").text

    empty = client.get("/platform/learners/1/integration")
    assert empty.status_code == 200
    assert "لم تُسجّل معطيات إدماج بعد" in empty.text

    first = followup(client, admin, 1)
    assert first.status_code == 303, first.text[:400]
    second = followup(
        client, admin, 1,
        outcome="vocational_training", observed_on="2026-11-12",
        organization="مؤسسة التكوين التجريبية",
        pathway_details="تكوين مهني مؤكد",
        intervention="تواصل مع إدارة التكوين",
        next_followup_on="2026-12-04",
    )
    assert second.status_code == 303
    individual = client.get("/platform/learners/1/integration").text
    assert "متابعة الدراسة" in individual
    assert "الالتحاق بتكوين مهني" in individual
    assert "في حاجة إلى المواكبة" in individual
    assert "مؤسسة التكوين التجريبية" in individual
    assert individual.index("2026-11-12") < individual.index("2026-11-06")
    assert "مستفيد الإدماج بطانة" not in individual

    overview = client.get("/platform/integration?track_id=1")
    assert overview.status_code == 200
    assert "ملفات تمت متابعتها" in overview.text
    assert "المتابعات ذات مسار إدماج مسجل" in overview.text
    assert "مستفيد الإدماج لعيايدة" in overview.text
    assert "مستفيد الإدماج بطانة" not in overview.text
    assert "الالتحاق بتكوين مهني" in overview.text
    assert "100.0%" in overview.text

    printed = client.get("/platform/learners/1/integration/print")
    assert printed.status_code == 200
    assert "size:A4 landscape" in printed.text
    assert "window.print()" in printed.text
    assert "no-store" in printed.headers["cache-control"]
    assert "مؤسسة التكوين التجريبية" in printed.text

    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Enrollment, Grade, IntegrationFollowup
    with Session(engine) as db:
        assert db.query(IntegrationFollowup).count() == 2
        assert db.query(Enrollment).count() == 2
        assert db.query(Grade).count() == 0
    signout(client, admin)


def test_center_scope_and_teachers_are_read_only(client):
    admin = setup_learners(client)
    assert followup(client, admin, 1).status_code == 303
    signout(client, admin)
    coord = signin(client, "coord1", PASSWORD)
    assert client.get("/platform/learners/1/integration").status_code == 200
    assert client.get("/platform/learners/2/integration").status_code == 403
    assert client.get("/platform/learners/2/integration/print").status_code == 403
    assert post(client, "/platform/learners/2/integration", coord,
                outcome="employed", observed_on="2026-11-06").status_code == 403
    assert client.get("/platform/integration?track_id=2").status_code == 403
    signout(client, coord)

    other = signin(client, "coord2", PASSWORD)
    assert client.get("/platform/learners/1/integration").status_code == 403
    assert client.get("/platform/integration?track_id=1").status_code == 403
    assert client.get("/platform/learners/2/integration").status_code == 200
    signout(client, other)

    teacher = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/learners/1/integration").status_code == 200
    assert 'action="/platform/learners/1/integration"' not in client.get("/platform/learners/1/integration").text
    assert client.get("/platform/integration").status_code == 403
    assert post(client, "/platform/learners/1/integration", teacher,
                outcome="employed", observed_on="2026-11-06").status_code == 403
    signout(client, teacher)
    assert client.get("/platform/learners/1/integration").status_code == 303


def test_invalid_outcomes_future_dates_and_length_rejected(client):
    admin = setup_learners(client)
    bad = [
        dict(outcome="other"),
        dict(observed_on="2090-11-06"),
        dict(next_followup_on="2026-11-05"),
        dict(organization="أ" * 181),
        dict(pathway_details="ب" * 301),
        dict(intervention="ج" * 501),
    ]
    for case in bad:
        result = followup(client, admin, 1, **case)
        assert result.status_code == 422, result.text[:250]
    assert client.get("/platform/learners/999/integration").status_code == 404
    assert client.get("/platform/learners/999/integration/print").status_code == 404


def test_learner_erasure_removes_followup_records(client):
    admin = setup_learners(client)
    assert followup(client, admin, 1).status_code == 303
    erase = post(client, "/platform/privacy/erase", admin,
                 learner_id=1, category="duplicate",
                 reason="تصحيح تسجيل مكرر بدون وثائق مرفقة",
                 confirmation="حذف نهائي")
    assert erase.status_code == 303, erase.text[:300]
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import IntegrationFollowup
    with Session(engine) as db:
        assert db.query(IntegrationFollowup).count() == 0
