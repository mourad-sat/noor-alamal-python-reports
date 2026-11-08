"""Photo upload, authorization, erasure, and print-format regression tests."""
import base64
import io
import re

from PIL import Image

from test_app import client
from test_platform_v2 import setup_org, signin, signout, post
from test_learner_track_cards import register


def make_photo(fmt="PNG"):
    image = Image.new("RGB", (640, 800), (31, 119, 157))
    data = io.BytesIO()
    image.save(data, format=fmt)
    return data.getvalue()


def send_photo(client, enrollment_id, csrf, raw, name="portrait.png", media="image/png"):
    return client.post(f"/platform/learners/{enrollment_id}/photo",
                       data={"_csrf": csrf}, files={"photo": (name, raw, media)})


def test_photo_saved_privately_and_shown_in_both_print_cards(client):
    admin = setup_org(client)
    en = register(client, admin, track_id=1, name="مستفيدة مع صورة", source="PHOTO-11")
    record = client.get(f"/platform/learners/{en}")
    assert record.status_code == 200
    for text in ("طباعة بطاقة A4 المفصلة", "طباعة البطاقة الصغيرة",
                 "حفظ الصورة", "image/webp"):
        assert text in record.text
    assert client.get(f"/platform/learners/{en}/photo/file").status_code == 404

    saved = send_photo(client, en, admin, make_photo())
    assert saved.status_code == 303, saved.text[:350]
    assert saved.headers["location"] == f"/platform/learners/{en}"
    image = client.get(f"/platform/learners/{en}/photo/file")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/jpeg")
    assert image.headers["cache-control"] == "private, no-store"
    with Image.open(io.BytesIO(image.content)) as photo:
        assert photo.size == (480, 600)
        assert photo.format == "JPEG"
        assert not photo.getexif()
    assert "photo/file" in client.get(f"/platform/learners/{en}").text

    a4 = client.get(f"/platform/learners/{en}/card")
    mini = client.get(f"/platform/learners/{en}/card/mini")
    assert a4.status_code == 200 and mini.status_code == 200
    assert "data:image/jpeg;base64," in a4.text
    assert "data:image/jpeg;base64," in mini.text
    for token in ("PHOTO-11", "مستفيدة مع صورة", "دار الشباب لعيايدة 2",
                  "التسويق الإلكتروني", "2026/27", "جمعية نور الأمل"):
        assert token in a4.text and token in mini.text
    for token in ("معلومات ولي الأمر", "عنوان", "تاريخ التسجيل",
                  "توقيع المسؤول", "خاتم الجمعية", "@page{size:A4"):
        assert token in a4.text
    for token in ("85.6mm", "54mm", "بطاقة داخلية", "البطاقة المفصلة A4"):
        assert token in mini.text
    # No sensitive guardian contacts or home address should appear on a mini ID card.
    assert "0622222222" not in mini.text
    assert "عنوان تجريبي 3" not in mini.text
    assert "0622222222" in a4.text

    # Photo can be replaced without creating multiple photo rows.
    second = send_photo(client, en, admin, make_photo("JPEG"), "portrait.jpg", "image/jpeg")
    assert second.status_code == 303
    from sqlalchemy.orm import Session
    from app import engine
    from platform_v2_models import LearnerPhoto
    with Session(engine) as db:
        assert db.query(LearnerPhoto).count() == 1

    wrong_confirmation = post(client, f"/platform/learners/{en}/photo/delete",
                              admin, confirmation="no")
    assert wrong_confirmation.status_code == 422
    removed = post(client, f"/platform/learners/{en}/photo/delete",
                   admin, confirmation="حذف الصورة")
    assert removed.status_code == 303
    assert client.get(f"/platform/learners/{en}/photo/file").status_code == 404
    assert "الصورة غير متوفرة" in client.get(f"/platform/learners/{en}/card").text


def test_photo_rejects_invalid_upload_and_wrong_csrf(client):
    admin = setup_org(client)
    en = register(client, admin, track_id=1, name="مستفيد رفض الصور", source="PHOTO-12")
    assert send_photo(client, en, "wrong", make_photo()).status_code == 403
    assert send_photo(client, en, admin, b"<svg><script>alert(1)</script></svg>").status_code == 422
    assert send_photo(client, en, admin, b"anything", "bad.svg", "image/svg+xml").status_code == 422
    assert send_photo(client, en, admin, b"\x00" * (2*1024*1024 + 1)).status_code == 413
    assert client.get(f"/platform/learners/{en}/photo/file").status_code == 404


def test_photo_and_full_cards_block_other_center_and_instructor(client):
    admin = setup_org(client)
    first = register(client, admin, track_id=1, name="مستفيد لعيايدة بالصورة", source="IMG-A")
    second = register(client, admin, track_id=2, name="مستفيد بطانة بالصورة", source="IMG-B")
    assert send_photo(client, first, admin, make_photo()).status_code == 303
    signout(client, admin)
    assert client.get(f"/platform/learners/{first}/card/mini").status_code == 303
    assert client.get(f"/platform/learners/{first}/photo/file").status_code == 303
    coord = signin(client, "coord2", "strong-test-password-12345")
    for url in (f"/platform/learners/{first}/card",
                f"/platform/learners/{first}/card/mini",
                f"/platform/learners/{first}/photo/file"):
        assert client.get(url).status_code == 403
    assert send_photo(client, first, coord, make_photo()).status_code == 403
    assert post(client, f"/platform/learners/{first}/photo/delete",
                coord, confirmation="حذف الصورة").status_code == 403
    assert client.get(f"/platform/learners/{second}/card/mini").status_code == 200
    signout(client, coord)
    teacher = signin(client, "trainer", "strong-test-password-12345")
    assert client.get(f"/platform/learners/{first}/card/mini").status_code == 403
    assert send_photo(client, first, teacher, make_photo()).status_code == 403
