"""Approved report erasure is an administrator-only audited operation."""
from test_app import client
from test_platform_v2 import setup_org, signin, signout, post

def test_admin_deletes_session_report_and_evidence_with_reason(client):
    admin=setup_org(client)
    signout(client,admin)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
        subject="مهني",weekday=4,starts_at="10:00",ends_at="12:00",
        reason="تكوين أسبوعي").status_code==303
    signout(client,coord)
    teacher=signin(client,"trainer","strong-test-password-12345")
    assert post(client,"/platform/sessions",teacher,slot_id=1,held_on="2026-11-06").status_code==303
    report=post(client,"/platform/sessions/1/report",teacher,
         subject="حصة مهنية",objectives="تتبع العمل",activities="نشاط",
         results="",challenges="",recommendations="")
    assert report.status_code==303
    attached=client.post("/platform/reports/1/attachments",data={"_csrf":teacher},
         files={"file":("pic.png",bytes.fromhex("89504e470d0a1a0a")+b"testimage","image/png")})
    assert attached.status_code==303
    forbidden=post(client,"/platform/reports/1/erase",teacher,
         reason="مبرر كاف لإزالة التقرير",confirmation="حذف التقرير")
    assert forbidden.status_code==403
    signout(client,teacher)
    admin=signin(client,"admin","correct-password")
    invalid=post(client,"/platform/reports/1/erase",admin,
         reason="سبب قصير",confirmation="حذف التقرير")
    assert invalid.status_code==422
    erased=post(client,"/platform/reports/1/erase",admin,
         reason="طلب حذف وثيقة موثق رسميًا",confirmation="حذف التقرير")
    assert erased.status_code==303,erased.text[:300]
    assert client.get("/platform/attachments/1/download").status_code==404
    from sqlalchemy.orm import Session
    from app import engine
    from platform_v2_models import SessionReport, Attachment, AttachmentBlob, Audit
    with Session(engine) as db:
        assert db.query(SessionReport).count()==0
        assert db.query(Attachment).count()==0
        assert db.query(AttachmentBlob).count()==0
        notes=db.query(Audit).filter(Audit.action=="session_report_erasure").all()
        assert len(notes)==1
        assert notes[0].resource_id==0
