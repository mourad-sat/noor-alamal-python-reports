"""One-time printable staff credential handover without persistent plaintext passwords."""
from sqlalchemy.orm import Session
from test_app import client, login
from test_platform_v2 import csrf_of, post, signin, signout


def test_new_staff_receives_private_printable_handover(client):
    csrf = login(client)
    listing = client.get("/platform/staff").text
    assert 'name="print_credentials" value="1"' in listing
    response = post(
        client, "/platform/staff", csrf, username="new-teacher",
        full_name="الأستاذة سلمى", role="instructor",
        password="First-login-secret-2026", print_credentials="1",
    )
    assert response.status_code == 200, response.text[:500]
    assert "طباعة البطاقات أو حفظ PDF" in response.text
    assert "الأستاذة سلمى" in response.text
    assert 'class="employee-card"' in response.text
    assert 'class="credential-slip"' in response.text
    assert "new-teacher" in response.text
    assert "First-login-secret-2026" in response.text
    assert "https://noor-alamal-python-reports.vercel.app/login" in response.text
    assert "غيّر كلمة المرور عند أول تسجيل دخول" in response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"

    # A later GET of the professional card must never disclose the password.
    card = client.get("/platform/staff/1/card")
    assert card.status_code == 200
    assert "الأستاذة سلمى" in card.text
    assert "First-login-secret-2026" not in card.text
    assert "credential-slip" not in card.text
    assert "new-teacher" not in card.text  # public professional card has no login details
    group = client.get("/platform/staff/cards")
    assert "First-login-secret-2026" not in group.text


def test_reset_prints_new_credentials_without_exposing_old_password(client):
    csrf = login(client)
    created = post(
        client, "/platform/staff", csrf, username="employee-alpha",
        full_name="الموظف الأول", role="educator", password="Old-password-credential-2026",
    )
    assert created.status_code == 303
    form = client.get("/platform/staff/1/edit")
    assert 'name="print_credentials" value="1"' in form.text
    new = post(
        client, "/platform/staff/1/password", csrf,
        password="New-password-credential-2026", print_credentials="1",
    )
    assert new.status_code == 200
    assert "employee-alpha" in new.text
    assert "New-password-credential-2026" in new.text
    assert "Old-password-credential-2026" not in new.text
    assert new.headers["cache-control"] == "no-store, private"
    assert "New-password-credential-2026" not in client.get("/platform/staff/1/card").text


def test_credential_handover_obeys_csrf_role_and_password_validation(client):
    csrf = login(client)
    rejected = post(
        client, "/platform/staff", "invalid", username="not-created",
        full_name="موظف", role="instructor", password="very-strong-password",
        print_credentials="1",
    )
    assert rejected.status_code == 403
    short = post(
        client, "/platform/staff", csrf, username="not-created",
        full_name="موظف", role="instructor", password="short",
        print_credentials="1",
    )
    assert short.status_code == 422
    created = post(
        client, "/platform/staff", csrf, username="created-teacher",
        full_name="أستاذ", role="instructor", password="strong-temporary-password",
    )
    assert created.status_code == 303  # Existing API behavior remains compatible.
    signout(client, csrf)
    staff_csrf = signin(client, "created-teacher", "strong-temporary-password")
    assert client.get("/platform/staff/1/card").status_code == 403
    denied = post(
        client, "/platform/staff/1/password", staff_csrf,
        password="Another-temporary-pass", print_credentials="1",
    )
    assert denied.status_code == 403


def test_printed_password_is_html_escaped_and_only_in_issued_post(client):
    csrf = login(client)
    secret = 'Long-Password-<svg>-2026'
    response = post(
        client, "/platform/staff", csrf, username="safe-account",
        full_name="<script>alert(1)</script>", role="coordinator",
        password=secret, print_credentials="1",
    )
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "Long-Password-&lt;svg&gt;-2026" in response.text
    assert secret not in response.text
    assert "Long-Password-&lt;svg&gt;-2026" not in client.get("/platform/staff/1/card").text
