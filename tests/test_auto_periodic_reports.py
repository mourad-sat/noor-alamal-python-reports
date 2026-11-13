"""Weekly/monthly generated reports use approved evidence only and retain review workflow."""
from datetime import date

from test_app import client
from test_platform_v2 import post, setup_org, signout, signin

PASSWORD = "strong-test-password-12345"


def setup_approved_session(client):
    admin = setup_org(client)
    assert post(client, "/platform/schedule", admin, track_id=1,
                staff_id=3, subject="مهني", weekday=4,
                starts_at="08:00", ends_at="10:00",
                reason="جدولة رسمية").status_code == 303
    signout(client, admin)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/sessions", teacher, slot_id=1,
                held_on="2026-11-06").status_code == 303
    assert post(client, "/platform/sessions/1/report", teacher,
                subject="التدريب على التواصل", objectives="تنمية مهارات التواصل",
                activities="تمارين جماعية في التواصل",
                results="إتقان خطوات التقديم",
                challenges="ضعف الموارد التقنية",
                recommendations="تعزيز الوسائل الرقمية").status_code == 303
    assert post(client, "/platform/reports/1/submit", teacher).status_code == 303
    signout(client, teacher)
    coord = signin(client, "coord1", PASSWORD)
    return coord


def test_generate_weekly_draft_only_approved_and_edit_then_approve(client):
    coord = setup_approved_session(client)
    reports_page = client.get("/platform/reports").text
    assert "توليد تقرير أسبوعي أو شهري تلقائياً" in reports_page
    # Before approval, no narrative can be promoted as completed work.
    first = post(client, "/platform/summary/auto", coord,
                 year_id=1, center_id=1, period_type="weekly",
                 anchor="2026-11-06")
    assert first.status_code == 303, first.text[:400]
    draft = client.get(first.headers["location"])
    assert draft.status_code == 200
    assert "لا توجد تقارير حصص مصادق عليها" in draft.text
    assert "تمارين جماعية في التواصل" not in draft.text

    assert post(client, "/platform/reports/1/review", coord,
                decision="approved").status_code == 303
    second = post(client, "/platform/summary/auto", coord,
                  year_id=1, center_id=1, period_type="weekly",
                  anchor="2026-11-06")
    assert second.status_code == 303
    assert second.headers["location"] == "/platform/summary/2/edit"
    rendered = client.get(second.headers["location"]).text
    assert "تمارين جماعية في التواصل" in rendered
    assert "ضعف الموارد التقنية" in rendered
    assert "تعزيز الوسائل الرقمية" in rendered
    assert "تقرير الحصة #1" in rendered
    assert "2026-11-02" in rendered
    assert "2026-11-08" in rendered
    assert post(client, "/platform/summary/2/edit", coord,
                analysis="خلاصة محررة من طرف منسق المركز").status_code == 303
    printed = client.get("/platform/summary/2/print")
    assert printed.status_code == 200
    assert "خلاصة محررة من طرف منسق المركز" in printed.text
    assert "مسودة غير مصادق عليها" in printed.text
    assert "توقيع منسق المركز" not in printed.text
    assert "no-store" in printed.headers["cache-control"]
    assert post(client, "/platform/summary/2/submit", coord).status_code == 303
    assert post(client, "/platform/summary/2/edit", coord,
                analysis="تغيير بعد الإرسال").status_code == 403
    signout(client, coord)
    admin = signin(client, "admin", "correct-password")
    assert post(client, "/platform/summary/2/review", admin,
                decision="approved").status_code == 303
    official = client.get("/platform/summary/2/print")
    assert "تمت المصادقة من طرف إدارة الجمعية" in official.text
    assert "توقيع منسق المركز" not in official.text


def test_monthly_draft_scoped_by_center_and_year(client):
    coord = setup_approved_session(client)
    assert post(client, "/platform/reports/1/review", coord,
                decision="approved").status_code == 303
    result = post(client, "/platform/summary/auto", coord,
                  year_id=1, center_id=1, period_type="monthly",
                  anchor="2026-11-14")
    assert result.status_code == 303
    assert "تمارين جماعية في التواصل" in client.get(result.headers["location"]).text
    assert "2026-11-01" in client.get(result.headers["location"]).text
    assert post(client, "/platform/summary/auto", coord,
                year_id=1, center_id=2, period_type="monthly",
                anchor="2026-11-14").status_code == 403
    assert client.get("/platform/summary/1/print").status_code == 200
    signout(client, coord)
    coord2 = signin(client, "coord2", PASSWORD)
    assert client.get("/platform/summary/1/print").status_code == 403
    assert client.get("/platform/summary/1/edit").status_code == 403
    signout(client, coord2)
    teacher = signin(client, "trainer", PASSWORD)
    assert post(client, "/platform/summary/auto", teacher,
                year_id=1, center_id=1, period_type="weekly",
                anchor="2026-11-06").status_code == 403
    assert client.get("/platform/summary/1/print").status_code == 403


def test_invalid_period_or_outside_year_rejected(client):
    admin = setup_org(client)
    for period, anchor in [("annual", "2026-11-06"),
                           ("weekly", "2026-01-06"),
                           ("monthly", "2028-01-06")]:
        response = post(client, "/platform/summary/auto", admin,
                        year_id=1, center_id=1, period_type=period,
                        anchor=anchor)
        assert response.status_code == 422


def test_print_has_indicator_graph_and_activity_table_from_approved_reports(client):
    coord = setup_approved_session(client)
    assert post(client, "/platform/reports/1/review", coord,
                decision="approved").status_code == 303
    response = post(client, "/platform/summary/auto", coord,
                    year_id=1, center_id=1, period_type="weekly",
                    anchor="2026-11-06")
    assert response.status_code == 303
    printed = client.get("/platform/summary/1/print")
    assert printed.status_code == 200
    for field in ("مؤشرات الفترة", "الحصص المعتمدة",
                  "مبيان توزيع سجلات الحضور والغياب",
                  "عدد الحصص المعتمدة حسب المسلك",
                  "جدول الأنشطة المنجزة والمصادق عليها",
                  "2026-11-06", "تمارين جماعية في التواصل",
                  "الصور التوثيقية المختارة"):
        assert field in printed.text, field
    assert "لا يمكن حساب نسبة الحضور" in printed.text
    assert '<div class="gallery">' in printed.text
    assert "لا توجد صور مختارة للإدراج" in printed.text


def test_only_selected_approved_image_is_embedded_in_report(client):
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Attachment, AttachmentBlob

    coord = setup_approved_session(client)
    assert post(client, "/platform/reports/1/review", coord,
                decision="approved").status_code == 303
    with Session(engine) as db:
        chosen = Attachment(
            report_id=1, original_name="نشاط داخل المركز.png",
            content_type="image/png", byte_size=8,
            storage_key="selected-demo", select_for_summary=True,
        )
        ignored = Attachment(
            report_id=1, original_name="صورة غير مختارة.png",
            content_type="image/png", byte_size=8,
            storage_key="unselected-demo", select_for_summary=False,
        )
        db.add_all([chosen, ignored])
        db.flush()
        db.add_all([
            AttachmentBlob(attachment_id=chosen.id, content=b"\\x89PNG\\r\\n\\x1a\\n"),
            AttachmentBlob(attachment_id=ignored.id, content=b"\\x89PNG\\r\\n\\x1a\\n"),
        ])
        db.commit()
    response = post(client, "/platform/summary/auto", coord,
                    year_id=1, center_id=1, period_type="monthly",
                    anchor="2026-11-06")
    assert response.status_code == 303
    html = client.get("/platform/summary/1/print").text
    assert "نشاط داخل المركز.png" in html
    assert 'src="/platform/attachments/1/view"' in html
    assert "صورة غير مختارة.png" not in html
    assert 'src="/platform/attachments/2/view"' not in html
    assert client.get("/platform/attachments/1/view").status_code == 200
    assert client.get("/platform/attachments/2/view").status_code == 403
    signout(client, coord)
    coord2 = signin(client, "coord2", PASSWORD)
    assert client.get("/platform/summary/1/print").status_code == 403
    assert client.get("/platform/attachments/1/view").status_code == 403
