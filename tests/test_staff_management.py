"""Regression tests for staff administration and printable ID cards."""
from test_app import client, login
from test_platform_v2 import setup_org, signin, signout, post


def test_staff_buttons_edit_and_card_printing(client):
    csrf = setup_org(client)
    listing = client.get("/platform/staff")
    assert listing.status_code == 200
    for text in ("تعديل", "حذف", "طباعة بطاقة", "طباعة جميع بطاقات الموظفين النشطين"):
        assert text in listing.text
    card = client.get("/platform/staff/1/card")
    assert card.status_code == 200
    assert "منسق لعيايدة" in card.text
    assert "دار الشباب لعيايدة 2" in card.text
    assert "جمعية نور الأمل" in card.text
    assert "/static/noor_official_logo.webp" in card.text
    assert "window.print()" in card.text
    assert "strong-test-password-12345" not in card.text
    assert client.get("/platform/staff/cards").text.count("employee-card") >= 4
    edit = client.get("/platform/staff/1/edit")
    assert edit.status_code == 200
    assert "مركز المنسق" in edit.text
    assert "كلمة مرور جديدة" in edit.text
    assert post(client, "/platform/staff/1/edit", "bad", username="coord1",
                full_name="منسق لعيايدة", role="coordinator", center_id=1).status_code == 403
    signout(client, csrf)
    assert client.get("/platform/staff/1/card").status_code == 303
    signin(client, "coord1", "strong-test-password-12345")
    assert client.get("/platform/staff/1/card").status_code == 403
    assert client.get("/platform/staff/1/edit").status_code == 403
    assert client.get("/platform/staff/1/delete").status_code == 403


def test_edit_coordinator_identity_and_center_conflicts(client):
    csrf = setup_org(client)
    # A center cannot silently be assigned two coordinators.
    rejected = post(client, "/platform/staff/1/edit", csrf, username="coord1",
                    full_name="المنسق الجديد", role="coordinator", center_id=2)
    assert rejected.status_code == 409
    # Editing name and username does not drop the center association.
    changed = post(client, "/platform/staff/1/edit", csrf, username="coord1_new",
                   full_name="منسق لعيايدة الجديد", role="coordinator", center_id=1)
    assert changed.status_code == 303, changed.text[:300]
    assert "منسق لعيايدة الجديد" in client.get("/platform/staff/1/card").text
    assert "coord1_new" in client.get("/platform/staff").text
    # Admin can remove an unused coordinator assignment and reassign it.
    unassign = post(client, "/platform/staff/1/edit", csrf, username="coord1_new",
                    full_name="منسق لعيايدة الجديد", role="coordinator", center_id=0)
    assert unassign.status_code == 303
    assert "لم يُحدد بعد" in client.get("/platform/staff/1/card").text
    assert post(client, "/platform/assign", csrf, staff_id=1, center_id=1, track_id=0, subject="").status_code == 303


def test_staff_delete_with_confirmation_and_no_history(client):
    csrf = setup_org(client)
    assert client.get("/platform/staff/1/delete").status_code == 200
    assert post(client, "/platform/staff/1/delete", "bad", confirm_username="coord1", reason="تبديل المنسق").status_code == 403
    assert post(client, "/platform/staff/1/delete", csrf, confirm_username="wrong", reason="تبديل المنسق").status_code == 422
    deleted = post(client, "/platform/staff/1/delete", csrf, confirm_username="coord1", reason="تبديل المنسق")
    assert deleted.status_code == 303
    assert client.get("/platform/staff/1/edit").status_code == 404
    assert client.get("/platform/staff/1/card").status_code == 404
    from app import engine
    from sqlalchemy.orm import Session
    from sqlalchemy import select, func
    from platform_v2_models import Assignment, Audit
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Assignment).where(Assignment.staff_id == 1)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(
            Audit.resource == "staff", Audit.resource_id == 1, Audit.action == "delete")) == 1
    # A replacement coordinator may now be assigned to the freed center.
    assert post(client, "/platform/staff", csrf, username="replacement", full_name="منسق بديل",
                role="coordinator", password="strong-test-password-12345").status_code == 303
    assert post(client, "/platform/assign", csrf, staff_id=5, center_id=1, track_id=0, subject="").status_code == 303


def test_historical_staff_records_are_protected_from_deletion(client):
    csrf = setup_org(client)
    signout(client, csrf)
    coordinator_csrf = signin(client, "coord1", "strong-test-password-12345")
    assert post(client, "/platform/schedule", coordinator_csrf, track_id=1,
                staff_id=3, subject="مهني", weekday=4, starts_at="08:00",
                ends_at="10:00", reason="البرنامج الأسبوعي").status_code == 303
    signout(client, coordinator_csrf)
    admin = login(client)
    assert "لا يمكن حذف" in client.get("/platform/staff/3/delete").text
    guarded = post(client, "/platform/staff/3/delete", admin,
                   confirm_username="trainer", reason="طلب الحذف")
    assert guarded.status_code == 409
    # Disabling keeps historical records and makes the ID card unavailable.
    assert post(client, "/platform/staff/3/toggle", admin).status_code == 303
    assert client.get("/platform/staff/3/card").status_code == 409
    assert "trainer" in client.get("/platform/staff").text


def test_card_html_escapes_staff_names(client):
    csrf = login(client)
    new = post(client, "/platform/staff", csrf, username="safe-id",
               full_name="<script>alert(1)</script>", role="educator",
               password="strong-test-password-12345")
    assert new.status_code == 303
    html = client.get("/platform/staff/1/card").text
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
