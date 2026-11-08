"""Employee-managed private card profiles and printed ID details."""
import base64
import io
import re

from PIL import Image
from test_app import client, login
from test_platform_v2 import setup_org, signin, signout, post


def upload(client, csrf, **fields):
    return client.post("/platform/profile", data={"_csrf": csrf, **fields})


def test_staff_can_edit_only_personal_card_fields(client):
    admin = setup_org(client)
    assert client.get("/platform/profile").status_code == 403
    assert client.get("/platform/my-card").status_code == 403
    signout(client, admin)
    employee = signin(client, "coord1", "strong-test-password-12345")
    page = client.get("/platform/profile")
    assert page.status_code == 200
    assert "التخصص" in page.text
    assert "photo" in page.text
    assert "معلومات بطاقتي" in client.get("/platform").text
    assert upload(client, "invalid", specialty="التنسيق التربوي").status_code == 403
    r = upload(client, employee, specialty="التنسيق التربوي",
               work_phone="+212 610-123456", work_email="staff@example.org")
    assert r.status_code == 303
    assert r.headers["location"] == "/platform/profile"
    own_card = client.get("/platform/my-card")
    assert own_card.status_code == 200
    for word in ["التنسيق التربوي", "+212 610-123456", "staff@example.org", "دار الشباب لعيايدة 2"]:
        assert word in own_card.text
    assert "strong-test-password-12345" not in own_card.text
    # The staff member cannot view another employee's card or change a colleague's profile.
    assert client.get("/platform/staff/2/card").status_code == 403
    assert client.get("/platform/staff/2/edit").status_code == 403
    signout(client, employee)
    admin = login(client)
    assert "staff@example.org" in client.get("/platform/staff/1/card").text
    assert "staff@example.org" in client.get("/platform/staff/cards").text
    assert "staff@example.org" not in client.get("/platform/staff/2/card").text


def test_staff_photo_validated_normalized_and_removed(client):
    admin = setup_org(client)
    signout(client, admin)
    employee = signin(client, "coord1", "strong-test-password-12345")
    buffer = io.BytesIO()
    Image.new("RGB", (600, 720), (235, 230, 211)).save(buffer, format="PNG")
    res = client.post("/platform/profile", data={"_csrf": employee, "specialty": "الأنشطة الموازية"},
                      files={"photo": ("photo.png", buffer.getvalue(), "image/png")})
    assert res.status_code == 303, res.text[:300]
    html = client.get("/platform/my-card").text
    assert 'data:image/jpeg;base64,' in html
    encoded = re.search(r'data:image/jpeg;base64,([A-Za-z0-9+/=]+)', html).group(1)
    normalized = base64.b64decode(encoded)
    with Image.open(io.BytesIO(normalized)) as portrait:
        assert portrait.format == "JPEG"
        assert portrait.size == (440, 520)
    bad = client.post("/platform/profile", data={"_csrf": employee},
                      files={"photo": ("bad.png", b"<svg><script>bad()</script></svg>", "image/png")})
    assert bad.status_code == 422
    bad_type = client.post("/platform/profile", data={"_csrf": employee},
                           files={"photo": ("bad.svg", b"<svg/>", "image/svg+xml")})
    assert bad_type.status_code == 422
    oversized = client.post("/platform/profile", data={"_csrf": employee},
                            files={"photo": ("big.jpg", b"a" * (2*1024*1024+1), "image/jpeg")})
    assert oversized.status_code == 413
    assert "data:image/jpeg;base64," in client.get("/platform/my-card").text
    removed = upload(client, employee, remove_photo="yes")
    assert removed.status_code == 303
    assert "data:image/jpeg;base64," not in client.get("/platform/my-card").text


def test_profile_fields_sanitized_and_admin_cannot_submit(client):
    admin = setup_org(client)
    assert upload(client, admin, specialty="admin impersonation").status_code == 403
    signout(client, admin)
    employee = signin(client, "coord2", "strong-test-password-12345")
    assert upload(client, employee, specialty="test", work_phone="javascript:alert(1)").status_code == 422
    assert upload(client, employee, specialty="test", work_email="invalid-address").status_code == 422
    assert upload(client, employee, specialty="<img src=x onerror=alert(1)>").status_code == 303
    printed = client.get("/platform/my-card").text
    assert "<img src=x onerror=alert(1)>" not in printed
    assert "&lt;img src=x onerror=alert(1)&gt;" in printed


def test_unused_staff_profile_deleted_with_staff_account(client):
    admin = setup_org(client)
    signout(client, admin)
    employee = signin(client, "coord1", "strong-test-password-12345")
    assert upload(client, employee, specialty="اختبار الملف").status_code == 303
    signout(client, employee)
    admin = login(client)
    deleted = post(client, "/platform/staff/1/delete", admin, confirm_username="coord1", reason="تعويض المنسق")
    assert deleted.status_code == 303
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import StaffProfile
    with Session(engine) as db:
        assert db.get(StaffProfile, 1) is None
