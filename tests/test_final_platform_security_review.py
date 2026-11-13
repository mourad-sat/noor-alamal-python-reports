"""Final hardening regression suite: sensitive routes, preview revocation, forms.

Uses only isolated disposable SQLite test data; never production records.
"""
from datetime import date
from test_app import client
from test_platform_v2 import setup_org, post, signin, signout

PASSWORD = "strong-test-password-12345"


def seed_selected_photo(client):
    admin = setup_org(client)
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import (
        Attachment, AttachmentBlob, SessionDay, SessionReport, Slot
    )
    with Session(engine) as db:
        slot = Slot(year_id=1, center_id=1, track_id=1, staff_id=3,
                    subject="التوثيق", kind="training", weekday=4,
                    starts_at="10:00", ends_at="12:00", canceled=False)
        db.add(slot)
        db.flush()
        day = SessionDay(slot_id=slot.id, held_on=date(2026, 11, 6),
                         status="held")
        db.add(day)
        db.flush()
        report = SessionReport(session_id=day.id, author_id=3,
                               subject="نشاط توثيقي", status="approved")
        db.add(report)
        db.flush()
        item = Attachment(report_id=report.id, storage_key="review-photo-1",
                          original_name="نشاط معتمد.png",
                          content_type="image/png", byte_size=8,
                          select_for_summary=True)
        db.add(item)
        db.flush()
        db.add(AttachmentBlob(attachment_id=item.id,
                              content=bytes.fromhex("89504e470d0a1a0a")))
        db.commit()
    return admin


def test_selected_image_preview_is_revoked_after_report_approval_withdrawn(client):
    admin = seed_selected_photo(client)
    selected = client.get("/platform/attachments/1/view")
    assert selected.status_code == 200
    assert "no-store" in selected.headers["cache-control"]
    assert selected.headers["x-content-type-options"] == "nosniff"

    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import SessionReport
    with Session(engine) as db:
        db.get(SessionReport, 1).status = "returned"
        db.commit()
    assert client.get("/platform/attachments/1/view").status_code == 403
    assert "نشاط معتمد.png" not in client.get("/platform/activity-album").text
    assert "نشاط معتمد.png" not in client.get("/platform/annual-report").text


def test_selected_image_preview_is_revoked_on_canceled_session_or_slot(client):
    seed_selected_photo(client)
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import SessionDay, Slot
    with Session(engine) as db:
        db.get(SessionDay, 1).status = "canceled"
        db.commit()
    assert client.get("/platform/attachments/1/view").status_code == 403
    with Session(engine) as db:
        db.get(SessionDay, 1).status = "held"
        db.get(Slot, 1).canceled = True
        db.commit()
    assert client.get("/platform/attachments/1/view").status_code == 403


def test_private_pages_roles_login_and_nav_visibility(client):
    admin = setup_org(client)
    admin_pages = [
        "/platform", "/platform/learners", "/platform/reports",
        "/platform/activity-album", "/platform/annual-report",
        "/platform/integration", "/platform/aggregates",
        "/platform/attendance/stats", "/platform/grades",
        "/platform/annual-report/print",
    ]
    for path in admin_pages:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code,
                                            response.text[:200])
    assert 'href="/platform/annual-report"' in client.get("/platform").text
    signout(client, admin)
    for path in admin_pages:
        assert client.get(path).status_code == 303, path

    coord = signin(client, "coord1", PASSWORD)
    for path in admin_pages:
        assert client.get(path).status_code == 200, path
    assert client.get("/platform/annual-report?center_id=2").status_code == 403
    assert client.get("/platform/annual-report/print?center_id=2").status_code == 403
    signout(client, coord)

    trainer = signin(client, "trainer", PASSWORD)
    for path in ("/platform/activity-album", "/platform/annual-report",
                 "/platform/annual-report/print", "/platform/integration"):
        assert client.get(path).status_code == 403, path
    nav = client.get("/platform").text
    assert 'href="/platform/annual-report"' not in nav
    assert client.get("/platform/learners").status_code == 200
    signout(client, trainer)


def test_photo_selection_csrf_protection_and_cross_center_deny(client):
    admin = seed_selected_photo(client)
    # Refuse a write without the hidden session-bound token.
    assert client.post("/platform/activity-album/1/select").status_code == 422
    assert client.post("/platform/activity-album/1/select",
                       data={"_csrf": "wrong-token"}).status_code == 403
    assert client.get("/platform/attachments/1/view").status_code == 200
    signout(client, admin)
    coord2 = signin(client, "coord2", PASSWORD)
    assert post(client, "/platform/activity-album/1/select", coord2).status_code == 403
    assert client.get("/platform/attachments/1/view").status_code == 403
    signout(client, coord2)


def test_recent_reports_have_mobile_and_print_layouts(client):
    setup_org(client)
    checks = [
        ("/platform/annual-report", "annual-kpis", "@media(max-width:620px)"),
        ("/platform/integration", "if-dashboard-grid", "@media(max-width:650px)"),
    ]
    for url, class_name, responsive in checks:
        response = client.get(url)
        assert response.status_code == 200
        assert class_name in response.text
        assert responsive in response.text
        assert "@media print" in response.text
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Enrollment, Learner
    from datetime import date
    with Session(engine) as db:
        learner = Learner(full_name="المستفيد التجريبي للجوال")
        db.add(learner)
        db.flush()
        db.add(Enrollment(learner_id=learner.id, year_id=1,
                          center_id=1, track_id=1,
                          enrolled_on=date(2026, 9, 1)))
        db.commit()
    skills = client.get("/platform/learners/1/skills")
    assert skills.status_code == 200
    assert "@media(max-width:600px)" in skills.text
    assert "@media print" in skills.text


def test_annual_report_query_volume_is_bounded_for_center_dashboard(client):
    setup_org(client)
    from test_performance_and_mobile import QueryCounter
    with QueryCounter() as counter:
        report = client.get("/platform/annual-report?year_id=1")
    assert report.status_code == 200
    assert counter.count <= 35, counter.count


def test_instructor_loses_attachment_access_after_assignment_revoked(client):
    admin = seed_selected_photo(client)
    signout(client, admin)
    trainer = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/attachments/1/download").status_code == 200
    assert client.get("/platform/attachments/1/view").status_code == 200
    from app import engine
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from platform_v2_models import Assignment
    with Session(engine) as db:
        assignment = db.scalar(select(Assignment).where(
            Assignment.staff_id == 3, Assignment.center_id == 1,
            Assignment.track_id == 1,
        ))
        assert assignment is not None
        db.delete(assignment)
        db.commit()
    assert client.get("/platform/attachments/1/download").status_code == 403
    assert client.get("/platform/attachments/1/view").status_code == 403
    assert client.get("/platform/reports/1/attachments").status_code == 403
    signout(client, trainer)
