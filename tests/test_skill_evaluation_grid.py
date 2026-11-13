"""Competency grid must be scoped, validated, printable and separate from grades."""
from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def setup_two_learners(client):
    admin = setup_org(client)
    for track, name in ((1, "مستفيد المهارات الأول"), (2, "مستفيد المهارات الثاني")):
        result = post(client, "/platform/learners", admin,
                      full_name=name, year_id=1, track_id=track)
        assert result.status_code == 303, result.text[:300]
    return admin


def test_skill_grid_three_stages_print_and_no_grade_mutation(client):
    admin = setup_two_learners(client)
    detail = client.get("/platform/learners/1")
    assert detail.status_code == 200
    assert "/platform/learners/1/skills" in detail.text
    assert "فتح شبكة تقييم المهارات" in detail.text

    empty = client.get("/platform/learners/1/skills")
    assert empty.status_code == 200
    assert "شبكة تقييم المهارات والكفايات" in empty.text
    assert "التقييم الأولي" in empty.text
    assert "التقييم المرحلي" in empty.text
    assert "التقييم النهائي" in empty.text
    assert "لم يُقيّم" in empty.text
    assert "مستفيد المهارات الثاني" not in empty.text

    initial = post(client, "/platform/learners/1/skills", admin,
                   skill_key="communication", stage="initial", level="1",
                   evaluated_on="2026-11-05", comment="يحتاج مواكبة في التعبير")
    assert initial.status_code == 303, initial.text[:300]
    final = post(client, "/platform/learners/1/skills", admin,
                 skill_key="communication", stage="final", level="4",
                 evaluated_on="2027-06-10", comment="تحسن ملموس في التواصل")
    assert final.status_code == 303
    monthly = client.get("/platform/learners/1/skills").text
    for word in ("التواصل والتعبير", "1/4", "4/4", "يحتاج مواكبة",
                 "تحسن ملموس", "width:25%", "width:100%"):
        assert word in monthly, word

    printed = client.get("/platform/learners/1/skills/print")
    assert printed.status_code == 200
    assert "size:A4 portrait" in printed.text
    assert "جمعية نور الأمل" in printed.text
    assert "window.print()" in printed.text
    assert "no-store" in printed.headers["cache-control"]
    assert "مستفيد المهارات الثاني" not in printed.text

    duplicate = post(client, "/platform/learners/1/skills", admin,
                     skill_key="communication", stage="initial", level="3",
                     evaluated_on="2026-11-05")
    assert duplicate.status_code == 409

    from app import engine
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from platform_v2_models import Grade, SkillEvaluation
    with Session(engine) as db:
        assert db.query(SkillEvaluation).count() == 2
        assert db.query(Grade).count() == 0
    signout(client, admin)


def test_skills_require_track_assignment_and_never_expose_other_center(client):
    admin = setup_two_learners(client)
    signout(client, admin)
    first = signin(client, "coord1", PASSWORD)
    assert client.get("/platform/learners/1/skills").status_code == 200
    assert client.get("/platform/learners/2/skills").status_code == 403
    assert client.get("/platform/learners/2/skills/print").status_code == 403
    assert post(client, "/platform/learners/2/skills", first,
                skill_key="digital", stage="initial", level="2",
                evaluated_on="2026-11-06").status_code == 403
    signout(client, first)

    second = signin(client, "coord2", PASSWORD)
    assert client.get("/platform/learners/1/skills").status_code == 403
    assert client.get("/platform/learners/2/skills").status_code == 200
    signout(client, second)

    teacher = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/learners/1/skills").status_code == 200
    assert post(client, "/platform/learners/1/skills", teacher,
                skill_key="digital", stage="mid", level="2",
                evaluated_on="2026-11-06").status_code == 303
    assert client.get("/platform/learners/1/skills/print").status_code == 200
    signout(client, teacher)
    assert client.get("/platform/learners/1/skills").status_code == 303


def test_bad_skill_level_stage_date_and_oversize_note_rejected(client):
    admin = setup_two_learners(client)
    attempts = [
        dict(skill_key="invalid",stage="initial",level="2",evaluated_on="2026-11-06"),
        dict(skill_key="digital",stage="invalid",level="2",evaluated_on="2026-11-06"),
        dict(skill_key="digital",stage="mid",level="5",evaluated_on="2026-11-06"),
        dict(skill_key="digital",stage="mid",level="2",evaluated_on="2025-11-06"),
        dict(skill_key="digital",stage="mid",level="2",evaluated_on="2026-11-06",
             comment="x"*601),
    ]
    for fields in attempts:
        response = post(client,"/platform/learners/1/skills",admin,**fields)
        assert response.status_code == 422, response.text[:300]
    assert client.get("/platform/learners/999/skills").status_code == 404
