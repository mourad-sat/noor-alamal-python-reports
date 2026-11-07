"""Integration coverage for the multi-center operational workflows."""
import re
from datetime import date, timedelta

from test_app import client, login


def csrf_of(client, url="/platform"):
    response = client.get(url)
    assert response.status_code == 200, response.text[:300]
    token = re.search(r'name="_csrf" value="([^"]+)"', response.text)
    assert token, response.text[:700]
    return token.group(1)


def signin(client, username, password):
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 303, response.text[:500]
    return csrf_of(client)


def signout(client, csrf):
    response = client.post("/logout", data={"_csrf": csrf})
    assert response.status_code == 303


def post(client, endpoint, csrf, **fields):
    return client.post(endpoint, data={"_csrf": csrf, **fields})


def setup_org(client):
    admin_csrf = login(client)
    assert client.get("/platform").status_code == 200
    y = post(client,"/platform/years",admin_csrf,label="2026/27",starts_on="2026-09-01",ends_on="2027-07-31")
    assert y.status_code == 303
    for center,name in [(1,"التسويق الإلكتروني"),(2,"التسويق الإلكتروني")]:
        r=post(client,"/platform/tracks",admin_csrf,year_id=1,center_id=center,name=name)
        assert r.status_code==303, r.text[:300]
    for username,name,role in [
        ("coord1","منسق لعيايدة","coordinator"),("coord2","منسق بطانة","coordinator"),
        ("trainer","المكوّن المشترك","instructor"),("educator","منشطة الرياضيات","educator"),
    ]:
        r=post(client,"/platform/staff",admin_csrf,username=username,full_name=name,role=role,password="strong-test-password-12345")
        assert r.status_code==303,r.text[:300]
    for staff,center,track,subject in [
        (1,1,0,""),(2,2,0,""),(3,1,1,""),(3,2,2,""),(4,1,1,"الرياضيات"),
    ]:
        r=post(client,"/platform/assign",admin_csrf,staff_id=staff,center_id=center,track_id=track,subject=subject)
        assert r.status_code==303,r.text[:500]
    return admin_csrf


def test_centers_roles_and_cross_center_schedule_conflicts(client):
    admin=setup_org(client)
    assert "مركز حمزة عبد المطلب" in client.get("/platform").text
    assert "دار الشباب لعيايدة 2" in client.get("/platform").text
    assert client.get("/platform/staff").status_code==200
    signout(client,admin)
    c1=signin(client,"coord1","strong-test-password-12345")
    assert client.get("/platform/staff").status_code==403
    ok=post(client,"/platform/schedule",c1,track_id=1,staff_id=3,
          subject="مهني",weekday=4,starts_at="14:00",ends_at="16:00",reason="الجدول الأسبوعي")
    assert ok.status_code==303,ok.text[:300]
    # Same group cannot attend two separate subjects at the same time.
    overlap_group=post(client,"/platform/schedule",c1,track_id=1,staff_id=4,
          subject="الرياضيات",weekday=4,starts_at="15:00",ends_at="16:30",reason="جدول تأهيل تربوي")
    assert overlap_group.status_code==409,overlap_group.text[:300]
    signout(client,c1)
    c2=signin(client,"coord2","strong-test-password-12345")
    conflict=post(client,"/platform/schedule",c2,track_id=2,staff_id=3,
          subject="مهني",weekday=4,starts_at="15:00",ends_at="17:00",reason="الجدول الأسبوعي")
    assert conflict.status_code==409,conflict.text[:300]
    assert client.get("/platform/learners").status_code==200
    signout(client,c2)
    instr=signin(client,"trainer","strong-test-password-12345")
    assert client.get("/platform/staff").status_code==403
    assert client.get("/reports").status_code==403
    assert client.get("/platform").status_code==200
    signout(client,instr)


def test_registration_attendance_permit_grade_review_and_notices(client):
    admin=setup_org(client)
    signout(client,admin)
    c1=signin(client,"coord1","strong-test-password-12345")
    enroll=post(client,"/platform/learners",c1,full_name="مستفيد تجريبي",gender="ذكر",
        born_on="2012-01-01",birthplace="الرباط",phone="",address="",
        guardian_name="ولي تجريبي",guardian_relationship="الأب",
        guardian_phone="0600000000",year_id=1,track_id=1)
    assert enroll.status_code==303,enroll.text[:300]
    assert "مستفيد تجريبي" in client.get("/platform/learners/1").text
    slot=post(client,"/platform/schedule",c1,track_id=1,staff_id=3,
          subject="مهني",weekday=4,starts_at="08:00",ends_at="10:00",reason="الجدول الأسبوعي")
    assert slot.status_code==303,slot.text[:300]
    signout(client,c1)
    instr=signin(client,"trainer","strong-test-password-12345")
    opened=post(client,"/platform/sessions",instr,slot_id=1,held_on="2026-11-06")
    assert opened.status_code==303,opened.text[:500]
    mark=post(client,"/platform/sessions/1/attendance",instr,enrollment_id=1,status="absent")
    assert mark.status_code==303,mark.text[:500]
    assert "غياب سابق معلق" in client.get("/platform/sessions/1").text
    forbidden_entry=post(client,"/platform/sessions/1/attendance",instr,enrollment_id=1,status="present")
    assert forbidden_entry.status_code==409,forbidden_entry.text[:300]
    bad=post(client,"/platform/learners/1/permit",instr,reason="توضيح طبي مكتوب")
    assert bad.status_code==403
    report=post(client,"/platform/sessions/1/report",instr,subject="حصة تجريبية",
        objectives="تجريب",activities="نشاط",results="النتائج",challenges="صعوبات",recommendations="توصيات")
    assert report.status_code==303,report.text[:500]
    import base64
    one_pixel=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/vFQAAAAASUVORK5CYII=")
    upload=client.post("/platform/reports/1/attachments",data={"_csrf":instr},
             files={"file":("evidence.png",one_pixel,"image/png")})
    assert upload.status_code==303,upload.text[:300]
    assert client.get("/platform/attachments/1/download").status_code==200
    assert post(client,"/platform/reports/1/submit",instr).status_code==303
    assessment=post(client,"/platform/assessments",instr,track_id=1,subject="مهني",
        title="اختبار أول",held_on="2026-11-06",coefficient="2")
    assert assessment.status_code==303,assessment.text[:500]
    grade=post(client,"/platform/grades",instr,assessment_id=1,enrollment_id=1,score="17.5")
    assert grade.status_code==303,grade.text[:500]
    signout(client,instr)
    c1=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/reports/1/review",c1,decision="approved",comment="").status_code==303
    aggregate_path="/platform/aggregates?period=monthly&anchor=2026-11-06&year_id=1"
    assert '/platform/attachments/1/view' not in client.get(aggregate_path).text
    assert post(client,"/platform/attachments/1/select",c1).status_code==303
    assert client.get("/platform/attachments/1/view").status_code==200
    assert '/platform/attachments/1/view' in client.get(aggregate_path).text
    assert post(client,"/platform/grades/1/approve",c1).status_code==303
    assert "17.50" in client.get("/platform/learners/1").text
    permit=post(client,"/platform/learners/1/permit",c1,reason="اطلعت على أسباب الغياب وقبلتها")
    assert permit.status_code==303,permit.text[:500]
    assert "أخضر: الغياب مسوى" in client.get("/platform/learners/1").text
    notify=post(client,"/platform/notices",c1,enrollment_id=1,kind="summons",body="يرجى حضور ولي الأمر للمركز")
    assert notify.status_code==303,notify.text[:500]
    issued=post(client,"/platform/notices/1/issue",c1,body="يرجى الحضور يوم الإثنين")
    assert issued.status_code==303,issued.text[:500]
    assert client.get("/platform/notices/1/print").status_code==200
    delivered=post(client,"/platform/notices/1/deliver",c1,delivered_on=date.today().isoformat())
    assert delivered.status_code==303,delivered.text[:500]
    signout(client,c1)
    instr=signin(client,"trainer","strong-test-password-12345")
    assert post(client,"/platform/grades/1/correction",instr,new_score="18",reason="خطأ في نقل النقطة").status_code==303
    signout(client,instr)
    c1=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/corrections/1/review",c1,decision="approved").status_code==303
    assert "18.00" in client.get("/platform/learners/1").text


def test_partial_excel_csv_import_and_qr_proof(client):
    import html
    import re
    admin=setup_org(client)
    rows=(
        "الاسم الكامل,الجنس,تاريخ الازدياد,هاتف ولي الأمر,اسم ولي الأمر\\n"
        "سارة الاختبار,أنثى,2011-02-03,0611111111,والد سارة\\n"
        "حسن الاختبار,ذكر,2013-01-08,,ولي حسن\\n"
    ).replace("\\n","\n").encode("utf-8-sig")
    response=client.post("/platform/import/preview",data={"_csrf":admin,"track_id":"1"},
         files={"file":("sample.csv",rows,"text/csv")})
    assert response.status_code==200,response.text[:600]
    assert "صفوف صالحة: 1" in response.text
    assert "مرفوضة: 1" in response.text
    match_payload=re.search('name="payload" value="([^"]+)"',response.text)
    match_signature=re.search('name="signature" value="([^"]+)"',response.text)
    assert match_payload and match_signature
    committed=post(client,"/platform/import/commit",admin,payload=html.unescape(match_payload.group(1)),
        signature=match_signature.group(1))
    assert committed.status_code==200,committed.text[:500]
    assert "تم إدخال 1 مستفيدًا" in committed.text
    assert "سارة الاختبار" in client.get("/platform/learners").text
    export=post(client,"/platform/exports/grades/print",admin,year_id=1,center_id=1,track_id=1)
    assert export.status_code==200,export.text[:500]
    assert "سارة الاختبار" in export.text
    assert 'class="qr"' in export.text
    found=re.search(r'https://noor-alamal-python-reports.vercel.app/verify/([A-Za-z0-9_-]+)',export.text)
    assert found,export.text[-2000:]
    verify=client.get("/verify/"+found.group(1))
    assert verify.status_code==200
    assert "البيانات الإلكترونية المعتمدة" in verify.text
    assert "سارة الاختبار" not in verify.text


def test_approved_attendance_is_locked_and_requires_coordinator_review(client):
    admin=setup_org(client)
    signout(client,admin)
    c1=signin(client,"coord1","strong-test-password-12345")
    learner=post(client,"/platform/learners",c1,full_name="آمنة للاختبار",gender="أنثى",
        born_on="2012-02-02",birthplace="سلا",phone="",address="",
        guardian_name="ولي آمنة",guardian_relationship="الأم",
        guardian_phone="0600000011",year_id=1,track_id=1)
    assert learner.status_code==303
    slot=post(client,"/platform/schedule",c1,track_id=1,staff_id=3,
          subject="مهني",weekday=4,starts_at="09:00",ends_at="10:30",reason="الجدول الأسبوعي")
    assert slot.status_code==303
    signout(client,c1)
    teacher=signin(client,"trainer","strong-test-password-12345")
    assert post(client,"/platform/sessions",teacher,slot_id=1,held_on="2026-11-06").status_code==303
    assert post(client,"/platform/sessions/1/attendance",teacher,enrollment_id=1,status="present").status_code==303
    report=post(client,"/platform/sessions/1/report",teacher,subject="تكوين في التسويق",
        objectives="التعلم",activities="التطبيق",results="المهارات",challenges="",recommendations="")
    assert report.status_code==303
    assert post(client,"/platform/reports/1/submit",teacher).status_code==303
    signout(client,teacher)
    c1=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/reports/1/review",c1,decision="approved",comment="").status_code==303
    signout(client,c1)
    teacher=signin(client,"trainer","strong-test-password-12345")
    locked=post(client,"/platform/sessions/1/attendance",teacher,enrollment_id=1,status="absent")
    assert locked.status_code==409
    assert "طلب تصحيح" in client.get("/platform/sessions/1").text
    requested=post(client,"/platform/attendance/1/correction",teacher,
        new_status="absent",reason="تم إدخال الحضور خطأ أثناء إتمام سجل الحصة")
    assert requested.status_code==303,requested.text[:450]
    signout(client,teacher)
    c1=signin(client,"coord1","strong-test-password-12345")
    review_page=client.get("/platform/attendance/corrections")
    assert review_page.status_code==200
    assert "تم إدخال الحضور خطأ" in review_page.text
    approved=post(client,"/platform/attendance/corrections/1/review",c1,decision="approved",comment="تم التحقق")
    assert approved.status_code==303
    assert "غياب معلق" in client.get("/platform/learners/1").text
