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
    bad=post(client,"/platform/learners/1/permit",instr,reason="توضيح طبي مكتوب")
    assert bad.status_code==403
    report=post(client,"/platform/sessions/1/report",instr,subject="حصة تجريبية",
        objectives="تجريب",activities="نشاط",results="النتائج",challenges="صعوبات",recommendations="توصيات")
    assert report.status_code==303,report.text[:500]
    assert post(client,"/platform/reports/1/submit",instr).status_code==303
    assessment=post(client,"/platform/assessments",instr,track_id=1,subject="مهني",
        title="اختبار أول",held_on="2026-11-06",coefficient="2")
    assert assessment.status_code==303,assessment.text[:500]
    grade=post(client,"/platform/grades",instr,assessment_id=1,enrollment_id=1,score="17.5")
    assert grade.status_code==303,grade.text[:500]
    signout(client,instr)
    c1=signin(client,"coord1","strong-test-password-12345")
    assert post(client,"/platform/reports/1/review",c1,decision="approved",comment="").status_code==303
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
