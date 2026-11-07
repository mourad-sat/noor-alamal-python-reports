"""Privacy-erasure regression: hard delete only after shared evidence review."""
from datetime import date
import re

from test_app import client, login
from test_platform_v2 import setup_org, signout, signin, post

def test_admin_erasure_cleans_personal_relations_and_invalidates_public_qr(client):
    admin=setup_org(client)
    learner=post(client,"/platform/learners",admin,
       full_name="مستفيد الخصوصية التجريبي",gender="ذكر",born_on="2012-01-01",
       birthplace="سلا",phone="",address="",
       guardian_name="ولي خصوصية",guardian_relationship="الأب",
       guardian_phone="0600000066",year_id=1,track_id=1)
    assert learner.status_code==303
    forbidden_reason=post(client,"/platform/privacy/erase",admin,learner_id=1,
       category="data_subject",reason="طلب قانوني موثق لحذف البيانات",confirmation="لا")
    assert forbidden_reason.status_code==422
    signout(client,admin)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
       subject="مهني",weekday=4,starts_at="09:00",ends_at="11:00",reason="الجدول الأسبوعي").status_code==303
    signout(client,coord)
    teacher=signin(client,"trainer","strong-test-password-12345")
    assert post(client,"/platform/sessions",teacher,slot_id=1,held_on="2026-11-06").status_code==303
    assert post(client,"/platform/sessions/1/attendance",teacher,enrollment_id=1,status="absent").status_code==303
    report=post(client,"/platform/sessions/1/report",teacher,
       subject="برنامج تكويني",objectives="تتبع مستفيد الخصوصية التجريبي",
       activities="تطبيقات عملية",results="",challenges="",recommendations="")
    assert report.status_code==303
    picture=(b"\\x89PNG\\r\\n\\x1a\\n" + b"img-evidence-for-test")
    uploaded=client.post("/platform/reports/1/attachments",data={"_csrf":teacher},
          files={"file":("session-privacy.png",picture,"image/png")})
    assert uploaded.status_code==303
    assert post(client,"/platform/reports/1/submit",teacher).status_code==303
    assert post(client,"/platform/assessments",teacher,track_id=1,subject="مهني",
        title="تقييم",held_on="2026-11-06",coefficient="1").status_code==303
    assert post(client,"/platform/grades",teacher,assessment_id=1,enrollment_id=1,score="16").status_code==303
    signout(client,teacher)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/reports/1/review",coord,decision="approved").status_code==303
    assert post(client,"/platform/grades/1/approve",coord).status_code==303
    assert post(client,"/platform/notices",coord,enrollment_id=1,
         kind="summons",body="استدعاء ولي خصوصية").status_code==303
    signout(client,coord)
    admin=signin(client,"admin","correct-password")
    printed=post(client,"/platform/exports/grades/print",admin,year_id=1,center_id=1,track_id=1)
    assert printed.status_code==200
    token=re.search(r'/verify/([A-Za-z0-9_-]{30,})',printed.text).group(1)
    assert "مطابقة للبيانات الرسمية" in client.get(f"/verify/{token}").text
    # Shared classroom attachment cannot be silently removed by learner deletion.
    blocked=post(client,"/platform/privacy/erase",admin,learner_id=1,
          category="data_subject",reason="طلب قانوني موثق لحذف البيانات",confirmation="حذف نهائي")
    assert blocked.status_code==409,blocked.text[:400]
    deleted_evidence=post(client,"/platform/attachments/1/delete",admin,
          reason="تم فحص المرفق واتخاذ القرار القانوني لحذفه",confirmation="حذف المرفق")
    assert deleted_evidence.status_code==303,deleted_evidence.text[:400]
    erased=post(client,"/platform/privacy/erase",admin,learner_id=1,
          category="data_subject",reason="طلب قانوني موثق لحذف البيانات",confirmation="حذف نهائي")
    assert erased.status_code==303,erased.text[:400]
    assert client.get("/platform/learners/1").status_code==404
    assert "مستفيد الخصوصية التجريبي" not in client.get("/platform/reports").text
    assert "مستفيد الخصوصية التجريبي" not in client.get("/platform/sessions/1").text
    assert "مستفيد الخصوصية التجريبي" not in client.get("/platform/aggregates?period=monthly&anchor=2026-11-06&year_id=1").text
    assert client.get("/platform/attachments/1/download").status_code==404
    assert "تعذر التحقق" in client.get(f"/verify/{token}").text
    from sqlalchemy.orm import Session
    from app import engine
    from platform_v2_models import (
        Learner, Enrollment, Grade, GradeCorrection, Attendance, Permit, Notice,
        DocumentProof, Attachment, AttachmentBlob, ErasureAggregate, Audit,
    )
    with Session(engine) as db:
        for model in (Learner, Enrollment, Grade, GradeCorrection, Attendance,
                      Permit, Notice, DocumentProof, Attachment, AttachmentBlob):
            assert db.query(model).count()==0,model.__name__
        counter=db.get(ErasureAggregate,1)
        assert counter.erased_learners==1
        assert counter.absent_sessions==1
        receipts=db.query(Audit).filter(Audit.action=="irreversible_erasure").all()
        assert len(receipts)==1
        assert receipts[0].resource_id==0
        assert "مستفيد الخصوصية التجريبي" not in receipts[0].reason


def test_non_admin_cannot_erase_learner_or_shared_evidence(client):
    admin=setup_org(client)
    signout(client,admin)
    coordinator=signin(client,"coord1","strong-test-password-12345")
    assert client.get("/platform/privacy/erase").status_code==403
    assert post(client,"/platform/privacy/erase",coordinator,learner_id=1,
       category="legal",reason="سبب يستوفي الحد الأدنى",confirmation="حذف نهائي").status_code==403
    assert post(client,"/platform/attachments/1/delete",coordinator,
       reason="سبب يستوفي الحد الأدنى",confirmation="حذف المرفق").status_code==403
