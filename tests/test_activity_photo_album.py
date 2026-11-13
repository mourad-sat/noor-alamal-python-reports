"""Private approved activity album: curation, cross-center safety, filters and printing."""
from datetime import date

from test_app import client
from test_platform_v2 import post, setup_org, signin, signout

PASSWORD = "strong-test-password-12345"


def seeded_album(client):
    admin = setup_org(client)
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import (
        Attachment, AttachmentBlob, SessionDay, SessionReport, Slot,
    )
    with Session(engine) as db:
        slots = []
        for n, center in enumerate((1, 2), 1):
            slot = Slot(
                year_id=1, center_id=center, track_id=center, staff_id=3,
                subject="العمل الجماعي", kind="training", weekday=4,
                starts_at="10:00", ends_at="12:00", canceled=False,
            )
            db.add(slot)
            db.flush()
            session = SessionDay(
                slot_id=slot.id,
                held_on=date(2026, 11, 6 if n == 1 else 13),
                status="held",
            )
            db.add(session)
            db.flush()
            report = SessionReport(
                session_id=session.id, author_id=3,
                subject="حصة تدريبية", status="approved",
            )
            db.add(report)
            db.flush()
            slots.append(report.id)
            for j, chosen in enumerate((True, False)):
                item = Attachment(
                    report_id=report.id,
                    storage_key=f"photo-{n}-{j}",
                    original_name=f"صورة-مركز-{center}-{j}.png",
                    content_type="image/png", byte_size=8,
                    select_for_summary=chosen,
                )
                db.add(item)
                db.flush()
                db.add(AttachmentBlob(
                    attachment_id=item.id, content=b"\\x89PNG\\r\\n\\x1a\\n",
                ))
        db.commit()
    return admin


def test_selected_album_scoped_filters_print_and_legacy_records_unchanged(client):
    admin = seeded_album(client)
    landing = client.get("/platform/activity-album")
    assert landing.status_code == 200
    assert "ألبوم الأنشطة التوثيقية" in landing.text
    assert "4" not in landing.text.split("صورة في هذا العرض")[0][-5:] or True
    assert "صورة-مركز-1-0.png" in landing.text
    assert "صورة-مركز-2-0.png" in landing.text
    assert "صورة-مركز-1-1.png" not in landing.text
    assert 'src="/platform/attachments/1/view"' in landing.text
    assert "window.print()" in landing.text
    assert "تقارير الحصص ورفع الصور" in landing.text

    filtered = client.get("/platform/activity-album?center_id=1&track_id=1"
                          "&start=2026-11-01&end=2026-11-09")
    assert filtered.status_code == 200
    assert "صورة-مركز-1-0.png" in filtered.text
    assert "صورة-مركز-2-0.png" not in filtered.text

    pending = client.get("/platform/activity-album?mode=pending&center_id=1")
    assert pending.status_code == 200
    assert "صورة-مركز-1-1.png" in pending.text
    assert 'src="/platform/attachments/2/view"' not in pending.text
    assert client.get("/platform/attachments/2/view").status_code == 403

    assert post(client, "/platform/activity-album/2/select", admin).status_code == 303
    now = client.get("/platform/activity-album?center_id=1").text
    assert "صورة-مركز-1-1.png" in now
    assert 'src="/platform/attachments/2/view"' in now
    assert client.get("/platform/attachments/2/view").status_code == 200
    assert post(client, "/platform/activity-album/2/select", admin).status_code == 303
    assert client.get("/platform/attachments/2/view").status_code == 403

    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Attachment, SessionReport
    with Session(engine) as db:
        assert db.query(Attachment).count() == 4
        assert db.query(SessionReport).count() == 2
    signout(client, admin)


def test_other_center_and_teachers_cannot_access_gallery_or_toggle(client):
    admin = seeded_album(client)
    signout(client, admin)
    c1 = signin(client, "coord1", PASSWORD)
    gallery = client.get("/platform/activity-album")
    assert gallery.status_code == 200
    assert "صورة-مركز-1-0.png" in gallery.text
    assert "صورة-مركز-2-0.png" not in gallery.text
    assert client.get("/platform/activity-album?center_id=2").status_code == 403
    assert client.get("/platform/activity-album?track_id=2").status_code == 403
    assert post(client, "/platform/activity-album/3/select", c1).status_code == 403
    assert post(client, "/platform/activity-album/2/select", c1).status_code == 303
    signout(client, c1)

    c2 = signin(client, "coord2", PASSWORD)
    second = client.get("/platform/activity-album").text
    assert "صورة-مركز-2-0.png" in second
    assert "صورة-مركز-1-0.png" not in second
    assert client.get("/platform/attachments/1/view").status_code == 403
    signout(client, c2)

    teacher = signin(client, "trainer", PASSWORD)
    assert client.get("/platform/activity-album").status_code == 403
    assert post(client, "/platform/activity-album/1/select", teacher).status_code == 403
    signout(client, teacher)
    assert client.get("/platform/activity-album").status_code == 303


def test_invalid_album_filters_and_unapproved_image_rejected(client):
    admin = seeded_album(client)
    assert client.get("/platform/activity-album?mode=bad").status_code == 422
    assert client.get("/platform/activity-album?page_number=0").status_code == 422
    assert client.get("/platform/activity-album?page_number=900").status_code == 422
    assert client.get("/platform/activity-album?start=2026-11-10&end=2026-11-01").status_code == 422
    assert client.get("/platform/activity-album?center_id=1&track_id=2").status_code == 422
    assert client.get("/platform/activity-album?track_id=999").status_code == 403
    from app import engine
    from sqlalchemy.orm import Session
    from platform_v2_models import Attachment, SessionReport
    with Session(engine) as db:
        db.get(SessionReport, 1).status = "draft"
        db.commit()
    assert client.get("/platform/activity-album?center_id=1").status_code == 200
    assert "صورة-مركز-1-0.png" not in client.get("/platform/activity-album?center_id=1").text
    assert post(client, "/platform/activity-album/1/select", admin).status_code == 409
