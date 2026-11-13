"""Private, center-scoped Massar and guardian contact roster and printable A4 output."""
from test_app import client
from test_platform_v2 import setup_org, post, signin, signout

PASSWORD = "strong-test-password-12345"


def create_contact(client, csrf, track_id, name, code, phone):
    saved = post(
        client, "/platform/learners", csrf, full_name=name,
        year_id=1, track_id=track_id, guardian_phone=phone,
    )
    assert saved.status_code == 303, saved.text[:500]
    en_id = int(saved.headers["location"].rsplit("/", 1)[-1])
    updated = post(
        client, f"/platform/learners/{en_id}/edit", csrf,
        full_name=name, source_code=code, guardian_phone=phone,
    )
    assert updated.status_code == 303, updated.text[:500]
    return en_id


def test_admin_can_view_and_print_scoped_contacts(client):
    admin = setup_org(client)
    en1 = create_contact(client, admin, 1, "مستفيد سعيد", "MASSAR-123", "0611223344")
    create_contact(client, admin, 2, "مستفيد مركز آخر", "MASSAR-456", "0655667788")
    listing = client.get("/platform/learners?track_id=1")
    assert listing.status_code == 200
    assert "لائحة رموز مسار وهواتف أولياء الأمور" in listing.text
    assert f"/platform/learners/contacts/print?track_id=1" in listing.text
    assert "مستفيد سعيد" in listing.text
    assert "MASSAR-123" in listing.text
    assert "0611223344" in listing.text
    assert "0655667788" not in listing.text
    assert f"/platform/learners/{en1}" in listing.text

    printed = client.get("/platform/learners/contacts/print?track_id=1")
    assert printed.status_code == 200
    assert "no-store" in printed.headers["cache-control"]
    for field in ("جمعية نور الأمل", "اسم المستفيد(ة)", "رمز مسار",
                  "هاتف ولي الأمر", "MASSAR-123", "0611223344",
                  "طباعة / حفظ PDF", "window.print()", "size:A4 portrait",
                  "/static/noor_official_logo.webp"):
        assert field in printed.text, field
    assert "مستفيد مركز آخر" not in printed.text
    assert "0655667788" not in printed.text


def test_coordinator_sees_only_own_center_and_teacher_never_sees_phones(client):
    admin = setup_org(client)
    create_contact(client, admin, 1, "اسم المركز الأول", "CODE-A", "0611111111")
    create_contact(client, admin, 2, "اسم المركز الثاني", "CODE-B", "0622222222")
    signout(client, admin)

    coord1 = signin(client, "coord1", PASSWORD)
    assert "0611111111" in client.get("/platform/learners?track_id=1").text
    assert "0611111111" in client.get("/platform/learners/contacts/print?track_id=1").text
    assert client.get("/platform/learners/contacts/print?track_id=2").status_code == 403
    assert "0622222222" not in client.get("/platform/learners?track_id=1").text
    signout(client, coord1)

    teacher = signin(client, "trainer", PASSWORD)
    roster = client.get("/platform/learners?track_id=1")
    assert roster.status_code == 200
    assert "اسم المركز الأول" in roster.text
    assert "0611111111" not in roster.text
    assert "لائحة رموز مسار وهواتف أولياء الأمور" not in roster.text
    assert "/platform/learners/contacts/print" not in roster.text
    assert client.get("/platform/learners/contacts/print?track_id=1").status_code == 403
    signout(client, teacher)
    assert client.get("/platform/learners/contacts/print?track_id=1").status_code == 303


def test_empty_contact_values_are_not_invented_and_bad_track_is_denied(client):
    admin = setup_org(client)
    saved = post(client, "/platform/learners", admin, full_name="مستفيد بدون رمز",
                 year_id=1, track_id=1)
    assert saved.status_code == 303
    html = client.get("/platform/learners/contacts/print?track_id=1").text
    assert "مستفيد بدون رمز" in html
    assert "<td dir=\"ltr\">—</td>" in html
    assert client.get("/platform/learners/contacts/print?track_id=9999").status_code == 403
