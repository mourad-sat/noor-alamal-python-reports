"""Annual report uses approved records, center scopes and defensible denominators."""
from datetime import date
from decimal import Decimal

from test_app import client
from test_platform_v2 import setup_org, signout, signin

PASSWORD = "strong-test-password-12345"


def annual_seed(client):
    admin = setup_org(client)
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import (
        Assessment, Attachment, AttachmentBlob, Attendance, Enrollment, Grade,
        IntegrationFollowup, Learner, SessionDay, SessionReport, SkillEvaluation,
        Slot,
    )
    with Session(engine) as db:
        first = Learner(full_name="المستفيد الأول التجريبي")
        second = Learner(full_name="المستفيد الثاني التجريبي")
        db.add_all([first, second])
        db.flush()
        en1 = Enrollment(learner_id=first.id, year_id=1, center_id=1,
                         track_id=1, enrolled_on=date(2026, 9, 1))
        en2 = Enrollment(learner_id=second.id, year_id=1, center_id=2,
                         track_id=2, enrolled_on=date(2026, 9, 1))
        db.add_all([en1, en2])
        db.flush()
        for center, track, enrollment in [(1, 1, en1), (2, 2, en2)]:
            slot = Slot(year_id=1, center_id=center, track_id=track,
                        staff_id=3, subject="مهني", kind="training",
                        weekday=4, starts_at="09:00", ends_at="11:00",
                        canceled=False)
            db.add(slot)
            db.flush()
            for approved, held_on in [(True, date(2026, 11, 6)),
                                      (False, date(2026, 11, 13))]:
                day = SessionDay(slot_id=slot.id, held_on=held_on, status="held")
                db.add(day)
                db.flush()
                report = SessionReport(
                    session_id=day.id, author_id=3, subject="موضوع التدريب",
                    activities=(f"نشاط معتمد مركز {center}" if approved else
                                f"نشاط غير معتمد مركز {center}"),
                    status="approved" if approved else "draft",
                )
                db.add(report)
                db.flush()
                db.add(Attendance(
                    session_id=day.id, enrollment_id=enrollment.id,
                    status="present" if approved else "absent",
                    recorded_by="اختبار",
                ))
                img = Attachment(
                    report_id=report.id,
                    storage_key=f"annual-photo-{center}-{held_on}",
                    original_name=f"توثيق-{center}-{'معتمد' if approved else 'مسودة'}.png",
                    content_type="image/png", byte_size=8,
                    select_for_summary=True,
                )
                db.add(img)
                db.flush()
                db.add(AttachmentBlob(
                    attachment_id=img.id, content=b"\\x89PNG\\r\\n\\x1a\\n",
                ))
            assessment = Assessment(
                track_id=track, staff_id=3, subject="مهني",
                title="اختبار ميداني", held_on=date(2026, 11, 6),
                coefficient=Decimal("2"),
            )
            db.add(assessment)
            db.flush()
            db.add(Grade(
                assessment_id=assessment.id, enrollment_id=enrollment.id,
                score=Decimal("14"), status="approved" if center == 1 else "pending",
            ))
            db.add(SkillEvaluation(
                enrollment_id=enrollment.id, skill_key="communication",
                stage="initial", level=2,
                evaluated_on=date(2026, 11, 6),
                author_id=3,
            ))
            db.add(IntegrationFollowup(
                enrollment_id=enrollment.id, outcome="needs_support",
                observed_on=date(2026, 11, 6),
                intervention="مواكبة", recorded_by="اختبار",
            ))
            db.add(IntegrationFollowup(
                enrollment_id=enrollment.id,
                outcome="vocational_training" if center == 1 else "unknown",
                observed_on=date(2026, 11, 12),
                intervention="تحديث الوضعية", recorded_by="اختبار",
            ))
        db.commit()
    return admin


def test_annual_report_admin_sees_both_centers_with_correct_approved_only_counts(client):
    admin = annual_seed(client)
    page = client.get("/platform/annual-report?year_id=1")
    assert page.status_code == 200, page.text[:500]
    for label in (
        "التقرير السنوي الشامل", "الحصيلة حسب المركز", "تطور الكفايات",
        "الإدماج بعد التكوين", "صور توثيقية مختارة",
        "المستفيدون المسجلون", "نشاط معتمد مركز 1",
        "نشاط معتمد مركز 2", "توثيق-1-معتمد.png", "توثيق-2-معتمد.png",
        "غير مصادق عليها إدارياً", "معدل وصفي", "متابعة الإدماج",
    ):
        assert label in page.text, label
    assert "نشاط غير معتمد" not in page.text
    assert "توثيق-1-مسودة.png" not in page.text
    assert "توثيق-2-مسودة.png" not in page.text
    assert "14.00" in page.text
    assert "100.0%" in page.text
    assert "2</strong>" in page.text

    printed = client.get("/platform/annual-report/print?year_id=1")
    assert printed.status_code == 200
    assert "size:A4 portrait" in printed.text
    assert "window.print()" in printed.text
    assert "no-store" in printed.headers["cache-control"]
    assert "جمعية نور الأمل" in printed.text
    assert 'src="/platform/attachments/1/view"' in printed.text
    assert 'src="/platform/attachments/3/view"' in printed.text
    signout(client, admin)


def test_coordinator_only_their_center_and_teacher_forbidden(client):
    admin = annual_seed(client)
    signout(client, admin)
    first = signin(client, "coord1", PASSWORD)
    limited = client.get("/platform/annual-report?year_id=1")
    assert limited.status_code == 200
    assert "نشاط معتمد مركز 1" in limited.text
    assert "نشاط معتمد مركز 2" not in limited.text
    assert "توثيق-2-معتمد" not in limited.text
    assert "المستفيد الثاني التجريبي" not in limited.text
    assert client.get("/platform/annual-report?year_id=1&center_id=2").status_code == 403
    assert client.get("/platform/annual-report/print?year_id=1&center_id=2").status_code == 403
    signout(client, first)
    second = signin(client, "coord2", PASSWORD)
    limited = client.get("/platform/annual-report?year_id=1")
    assert limited.status_code == 200
    assert "نشاط معتمد مركز 2" in limited.text
    assert "نشاط معتمد مركز 1" not in limited.text
    signout(client, second)
    teacher = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/annual-report").status_code == 403
    assert client.get("/platform/annual-report/print").status_code == 403
    signout(client, teacher)
    assert client.get("/platform/annual-report").status_code == 303


def test_annual_year_validation_and_read_only(client):
    admin = annual_seed(client)
    assert client.get("/platform/annual-report?year_id=999").status_code == 404
    assert client.get("/platform/annual-report?year_id=1&center_id=999").status_code == 403
    assert client.get("/platform/annual-report?year_id=1&center_id=1").status_code == 200
    assert client.get("/platform/annual-report?year_id=1&center_id=0").status_code == 200
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Grade, SessionReport, IntegrationFollowup
    with Session(engine) as db:
        assert db.query(SessionReport).count() == 4
        assert db.query(Grade).count() == 2
        assert db.query(IntegrationFollowup).count() == 4
    signout(client, admin)
