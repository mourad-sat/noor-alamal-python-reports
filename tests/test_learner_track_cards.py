"""Navigation and print-card safeguards for the learner registry."""
from test_app import client, login
from test_platform_v2 import setup_org, post, signout, signin


def register(client, csrf, *, track_id, name, source="CODE-001"):
    response = post(client, "/platform/learners", csrf,
        full_name=name, gender="أنثى", born_on="2011-05-19",
        birthplace="سلا", phone="0611111111", address="عنوان تجريبي 3",
        guardian_name="ولي تجريبي", guardian_relationship="الأم",
        guardian_phone="0622222222", year_id=1, track_id=track_id)
    assert response.status_code == 303, response.text[:400]
    # Original school code can be completed manually from the protected edit page.
    en_id = int(response.headers["location"].rsplit("/", 1)[-1])
    edit = post(client, f"/platform/learners/{en_id}/edit", csrf,
        full_name=name, source_code=source, gender="أنثى",
        born_on="2011-05-19", birthplace="سلا", phone="0611111111",
        address="عنوان تجريبي 3", guardian_name="ولي تجريبي",
        guardian_relationship="الأم", guardian_phone="0622222222")
    assert edit.status_code == 303
    return en_id


def test_learner_names_only_show_after_track_selection(client):
    admin = setup_org(client)
    one = register(client, admin, track_id=1, name="مستفيدة مركز لعيايدة", source="A-001")
    two = register(client, admin, track_id=2, name="مستفيدة مركز بطانة", source="B-002")
    home = client.get("/platform/learners")
    assert home.status_code == 200
    assert "دار الشباب لعيايدة 2" in home.text
    assert "مركز حمزة عبد المطلب" in home.text
    assert "اختيار المركز والمسلك" in home.text
    assert "مستفيدة مركز لعيايدة" not in home.text
    assert "مستفيدة مركز بطانة" not in home.text
    assert "تسجيل مستفيد في سنة ومسلك واحد" not in home.text
    assert '/platform/learners?track_id=1' in home.text
    first = client.get("/platform/learners?track_id=1")
    assert first.status_code == 200
    assert "مستفيدة مركز لعيايدة" in first.text
    assert "مستفيدة مركز بطانة" not in first.text
    assert f"/platform/learners/{one}/card" in first.text
    assert "عدد المستفيدين في هذا المسلك: 1" in first.text
    assert f'name="track_id" value="1"' in first.text
    other = client.get("/platform/learners?track_id=2")
    assert other.status_code == 200
    assert "مستفيدة مركز بطانة" in other.text
    assert "مستفيدة مركز لعيايدة" not in other.text
    assert f"/platform/learners/{two}/card" in other.text


def test_card_print_contains_full_learner_identity_and_registration_data(client):
    admin = setup_org(client)
    en_id = register(client, admin, track_id=1, name="مستفيدة نموذجية", source="Z-123")
    page = client.get(f"/platform/learners/{en_id}")
    assert 'طباعة بطاقة المستفيد' in page.text
    printable = client.get(f"/platform/learners/{en_id}/card")
    assert printable.status_code == 200
    for piece in (
        "جمعية نور الأمل", "برنامج الفرصة الثانية", "بطاقة معلومات المستفيد",
        "مستفيدة نموذجية", "Z-123", "أنثى", "2011-05-19", "سلا",
        "0611111111", "عنوان تجريبي 3", "ولي تجريبي", "الأم",
        "0622222222", "دار الشباب لعيايدة 2", "التسويق الإلكتروني",
        "2026/27", "تاريخ التسجيل", "رقم الملف داخل المنصة",
        "طباعة البطاقة أو حفظ PDF", "window.print()", "size:A4",
        "/static/noor_official_logo.webp", "توقيع المسؤول", "خاتم الجمعية"
    ):
        assert piece in printable.text, piece
    assert "strong-test-password-12345" not in printable.text
    assert "سياسة" not in printable.text


def test_private_full_print_restricted_to_admin_and_center_coordinator(client):
    admin = setup_org(client)
    register(client, admin, track_id=1, name="مستفيد لعيايدة", source="A-123")
    register(client, admin, track_id=2, name="مستفيد بطانة", source="B-456")
    signout(client, admin)
    assert client.get("/platform/learners/1/card").status_code == 303
    coord1 = signin(client, "coord1", "strong-test-password-12345")
    allowed = client.get("/platform/learners/1/card")
    assert allowed.status_code == 200
    assert client.get("/platform/learners/2/card").status_code == 403
    assert client.get("/platform/learners?track_id=2").status_code == 403
    picker = client.get("/platform/learners")
    assert 'href="/platform/learners?track_id=1"' in picker.text
    assert 'href="/platform/learners?track_id=2"' not in picker.text
    assert "مستفيد لعيايدة" not in picker.text
    signout(client, coord1)
    teacher = signin(client, "trainer", "strong-test-password-12345")
    assert client.get("/platform/learners/1/card").status_code == 403
    selected = client.get("/platform/learners?track_id=1")
    assert selected.status_code == 200
    assert "مستفيد لعيايدة" in selected.text
    assert "طباعة البطاقة" not in selected.text
    signout(client, teacher)


def test_print_unknown_and_blank_values_are_safe(client):
    admin = setup_org(client)
    response = post(client, "/platform/learners", admin,
        full_name="مستفيدة بدون معلومات", gender="", born_on="",
        birthplace="", phone="", address="", guardian_name="",
        guardian_relationship="", guardian_phone="", year_id=1, track_id=1)
    assert response.status_code == 303
    id_ = int(response.headers["location"].rsplit("/", 1)[-1])
    result = client.get(f"/platform/learners/{id_}/card")
    assert result.status_code == 200
    assert "لم تُستكمل بعد" in result.text
    assert "مستفيدة بدون معلومات" in result.text
    assert client.get("/platform/learners/9999/card").status_code == 404
    assert client.get("/platform/learners?track_id=9999").status_code == 403
