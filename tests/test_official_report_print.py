"""Official session report: electronic approval + manual signature + private QR."""
import re
from test_app import client
from test_platform_v2 import setup_org, signin, signout, post

def test_approved_session_report_print_has_official_logo_and_qr(client):
    admin=setup_org(client)
    signout(client,admin)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/schedule",coord,track_id=1,staff_id=3,
        subject="مهني",weekday=4,starts_at="10:00",ends_at="12:00",
        reason="حصص التدريب الأسبوعي").status_code==303
    signout(client,coord)
    teacher=signin(client,"trainer","strong-test-password-12345")
    assert post(client,"/platform/sessions",teacher,slot_id=1,held_on="2026-11-06").status_code==303
    assert post(client,"/platform/sessions/1/report",teacher,
        subject="التكوين والهوية",objectives="أهداف التدريب الخاصة",
        activities="العمل الجماعي",results="تنمية المهارة",challenges="",recommendations="").status_code==303
    assert post(client,"/platform/reports/1/print",teacher).status_code==409
    assert post(client,"/platform/reports/1/submit",teacher).status_code==303
    signout(client,teacher)
    coord=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/reports/1/review",coord,decision="approved").status_code==303
    official=post(client,"/platform/reports/1/print",coord)
    assert official.status_code==200,official.text[:350]
    assert "إثبات المصادقة الإلكترونية" in official.text
    assert "منسق لعيايدة" in official.text
    assert "ختم الجمعية" in official.text
    assert "/static/noor_official_logo.webp" in official.text
    token=re.search(r'/verify/([A-Za-z0-9_-]{30,})',official.text).group(1)
    checked=client.get(f"/verify/{token}")
    assert checked.status_code==200
    assert "البيانات الإلكترونية المعتمدة لم تتغير" in checked.text
    assert "أهداف التدريب الخاصة" not in checked.text
    from sqlalchemy.orm import Session
    from app import engine
    from platform_v2_models import SessionReport
    with Session(engine) as db:
        report=db.get(SessionReport,1)
        report.activities="تغيير غير مصرح به في البيانات"
        db.commit()
    invalid=client.get(f"/verify/{token}")
    assert invalid.status_code==200
    assert "تعذر التحقق" in invalid.text
