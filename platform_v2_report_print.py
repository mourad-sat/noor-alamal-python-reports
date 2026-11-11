"""Formal, printable and electronically verified approved session report."""
from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, e, log
from platform_v2_attachments import report_access
from platform_v2_exports import BASE_URL, qr_svg, report_fingerprint
from platform_v2_models import (
    Attendance, Center, Enrollment, Learner, SessionDay, SessionReportProof,
    Slot, Staff, Track, Year,
)

@app.get("/platform/reports/{report_id}/print-draft", response_class=HTMLResponse)
def print_preliminary_session_report(report_id: int, request: Request,
                                     db: Session = Depends(get_db),
                                     user=Depends(actor)):
    """Unapproved report preview; no approval proof, electronic stamp or QR.

    The ordinary report permissions apply equally to the printable preview.
    """
    report, slot = report_access(db, user, report_id)
    if report.status not in ("draft", "submitted", "returned"):
        raise HTTPException(409, "هذا التقرير معتمد؛ استعمل طباعة التقرير الرسمي")
    session = db.get(SessionDay, report.session_id)
    center = db.get(Center, slot.center_id)
    track = db.get(Track, slot.track_id)
    year = db.get(Year, slot.year_id)
    instructor = db.get(Staff, slot.staff_id)
    marks = db.scalars(
        select(Attendance).where(Attendance.session_id == session.id)
    ).all()
    present = sum(mark.status in ("present", "late") for mark in marks)
    absent = sum(mark.status == "absent" for mark in marks)
    fields = (
        ("الأهداف المسطرة", report.objectives),
        ("سير الحصة والأنشطة", report.activities),
        ("النتائج والمهارات المكتسبة", report.results),
        ("الصعوبات والملاحظات", report.challenges),
        ("التوصيات والإجراءات", report.recommendations),
    )
    sections = "".join(
        '<section><h3>' + e(label) + '</h3>'
        '<p>' + (e(body) if body else "لا توجد ملاحظات") + '</p></section>'
        for label, body in fields
    )
    label = {
        "draft": "مسودة قيد الإعداد",
        "submitted": "مرسل للمراجعة",
        "returned": "أُعيد للتصحيح",
    }[report.status]
    comment = (
        '<section><h3>ملاحظة المنسق</h3><p>' + e(report.review_comment) + '</p></section>'
        if report.status == "returned" and report.review_comment else ""
    )
    html = f'''<!doctype html><html dir="rtl" lang="ar"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex,nofollow,noarchive">
    <meta name="referrer" content="no-referrer">
    <title>نسخة أولية غير معتمدة — تقرير حصة رقم {report.id}</title>
    <style>
      body{{font-family:Tahoma,Arial,sans-serif;background:#ecf1f7;color:#193145;margin:0;line-height:1.8}}
      .toolbar{{padding:12px;background:#293e58;text-align:center;color:#fff}}
      .toolbar button{{font:inherit;padding:8px 24px;border:0;border-radius:8px;cursor:pointer}}
      .sheet{{position:relative;box-sizing:border-box;max-width:210mm;margin:18px auto;
        background:#fff;min-height:280mm;padding:18mm 16mm;box-shadow:0 4px 14px #0002}}
      .sheet header{{display:flex;justify-content:space-between;align-items:center;
        gap:10px;border-bottom:3px solid #d78635;padding-bottom:12px}}
      header img{{width:95px;height:85px;object-fit:contain}}
      h1{{font-size:23px;margin:0}}h2{{font-size:19px;text-align:center;color:#7b4a22}}
      h3{{font-size:15px;margin:0;color:#375a7b}}
      p{{white-space:pre-wrap;overflow-wrap:anywhere}}
      .warning{{border:2px solid #b45e34;background:#fff5e7;color:#8f361b;
        padding:12px;text-align:center;font-size:19px;font-weight:900;
        margin:17px 0;break-inside:avoid}}
      .status{{text-align:center;font-weight:700;font-size:13px;color:#844621}}
      .meta{{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px;margin:18px 0}}
      .meta div{{border-bottom:1px solid #e0e8ef;padding:5px}}
      section{{margin-top:17px;break-inside:avoid}}
      .notice{{border-top:1px dashed #b45e34;margin-top:28px;padding-top:10px;
        font-size:12px;color:#8f361b;font-weight:700;break-inside:avoid}}
      @media print{{@page{{size:A4;margin:0}}body{{background:#fff}}
        .toolbar{{display:none}}.sheet{{margin:0;max-width:none;min-height:297mm;box-shadow:none}}
        .warning{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}}}
    </style></head><body>
    <div class="toolbar"><button type="button" onclick="window.print()">طباعة النسخة الأولية / حفظ PDF</button></div>
    <article class="sheet"><header><div><h1>جمعية نور الأمل</h1>
      <div>برنامج الفرصة الثانية – الجيل الجديد</div>
      <div>{e(center.name)}</div></div>
      <img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل"></header>
    <div class="warning" role="note">نسخة أولية غير معتمدة — ليست وثيقة رسمية</div>
    <h2>تقرير حصة أولي · رقم {report.id}</h2>
    <p class="status">الحالة: {e(label)}</p>
    <div class="meta"><div>السنة: <b>{e(year.label)}</b></div>
      <div>المسلك: <b>{e(track.name)}</b></div>
      <div>المادة: <b>{e(slot.subject)}</b></div>
      <div>تاريخ الحصة: <b>{e(session.held_on)}</b></div>
      <div>المؤطر: <b>{e(instructor.full_name)}</b></div>
      <div>الساعة: <b>{e(slot.starts_at)}–{e(slot.ends_at)}</b></div>
      <div>عدد الحاضرين: <b>{present}</b></div>
      <div>عدد الغائبين: <b>{absent}</b></div></div>
    <h3>عنوان الحصة: {e(report.subject)}</h3>
    {sections}{comment}
    <div class="notice">هذه النسخة للاطلاع أو المراجعة فقط. لا تثبت المصادقة الإلكترونية،
    ولا تحتوي على رمز تحقق أو ختم اعتماد. تصدر النسخة الرسمية بعد مصادقة المنسق.</div>
    </article></body></html>'''
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store, private",
        "Pragma": "no-cache",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
    })


@app.post("/platform/reports/{report_id}/print",response_class=HTMLResponse)
def print_approved_session_report(report_id:int,request:Request,
           token:str=Form(alias="_csrf"),
           db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    report,slot=report_access(db,user,report_id)
    if report.status!="approved" or not report.reviewed_at or not report.reviewed_by:
        raise HTTPException(409,"لا يمكن إصدار تقرير رسمي قبل المصادقة الإلكترونية")
    session=db.get(SessionDay,report.session_id)
    center=db.get(Center,slot.center_id)
    track=db.get(Track,slot.track_id)
    year=db.get(Year,slot.year_id)
    instructor=db.get(Staff,slot.staff_id)
    attendance=db.scalars(select(Attendance).where(Attendance.session_id==session.id)).all()
    present=sum(a.status in ("present","late") for a in attendance)
    absent=sum(a.status=="absent" for a in attendance)
    token_value=secrets.token_urlsafe(36)
    proof=SessionReportProof(
        token_hash=hashlib.sha256(token_value.encode()).hexdigest(),
        report_id=report.id,fingerprint=report_fingerprint(db,report))
    db.add(proof)
    db.flush()
    log(db,user,"official_report_print","session_report",report.id)
    db.commit()
    verify_url=BASE_URL+"/verify/"+token_value
    code=qr_svg(verify_url)
    fields=[
        ("الأهداف المسطرة",report.objectives),
        ("سير الحصة والأنشطة",report.activities),
        ("النتائج والمهارات المكتسبة",report.results),
        ("الصعوبات والملاحظات",report.challenges),
        ("التوصيات والإجراءات",report.recommendations),
    ]
    sections="".join(
        f'<section><h3>{e(label)}</h3><p>{e(body) if body else "لا توجد ملاحظات"}</p></section>'
        for label,body in fields
    )
    issued=report.reviewed_at.strftime("%Y-%m-%d %H:%M UTC")
    html=f'''<!doctype html><html dir="rtl" lang="ar"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>التقرير الرسمي للحصة رقم {report.id}</title>
    <style>
      body{{font-family:Tahoma,Arial,sans-serif;background:#edf2f7;color:#193145;margin:0;line-height:1.8}}
      .toolbar{{padding:12px;background:#155995;text-align:center;color:white}}
      button{{font:inherit;padding:8px 25px;border:0;border-radius:7px;cursor:pointer}}
      .sheet{{box-sizing:border-box;max-width:210mm;margin:18px auto;background:white;
        min-height:280mm;padding:18mm 16mm;box-shadow:0 4px 14px #0001}}
      header{{display:flex;align-items:center;justify-content:space-between;
        border-bottom:3px solid #19a24d;padding-bottom:12px;gap:10px}}
      header img{{width:95px;height:85px;object-fit:contain}}
      h1{{font-size:23px;margin:0}}h2{{font-size:19px;text-align:center;color:#14639f}}
      h3{{font-size:15px;margin:0;color:#2067a2}}
      p{{white-space:pre-wrap;overflow-wrap:anywhere}}
      .meta{{display:grid;grid-template-columns:1fr 1fr;gap:4px 18px;margin:18px 0}}
      .meta div{{border-bottom:1px solid #e0e8ef;padding:5px}}
      section{{margin-top:17px;break-inside:avoid}}.approval{{margin-top:28px;border:1px solid #b7d9c2;
        padding:12px;break-inside:avoid}}.qr svg{{width:94px;height:94px}}
      footer{{margin-top:40px;display:flex;justify-content:space-between;gap:10px;break-inside:avoid}}
      @media print{{@page{{size:A4;margin:0}}body{{background:white}}.toolbar{{display:none}}
        .sheet{{margin:0;max-width:none;min-height:297mm;box-shadow:none}}}}
    </style></head><body>
    <div class="toolbar"><button type="button" onclick="window.print()">طباعة / حفظ PDF الرسمي</button></div>
    <article class="sheet"><header><div><h1>جمعية نور الأمل</h1>
      <div>برنامج الفرصة الثانية – الجيل الجديد</div><div>{e(center.name)}</div></div>
      <img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل"></header>
    <h2>تقرير حصة معتمد إلكترونيًا · رقم {report.id}</h2>
    <div class="meta"><div>السنة: <b>{e(year.label)}</b></div>
      <div>المسلك: <b>{e(track.name)}</b></div>
      <div>المادة: <b>{e(slot.subject)}</b></div>
      <div>تاريخ الحصة: <b>{e(session.held_on)}</b></div>
      <div>المؤطر: <b>{e(instructor.full_name)}</b></div>
      <div>الساعة: <b>{e(slot.starts_at)}–{e(slot.ends_at)}</b></div>
      <div>عدد الحاضرين: <b>{present}</b></div>
      <div>عدد الغائبين: <b>{absent}</b></div></div>
    <h3>عنوان الحصة: {e(report.subject)}</h3>
    {sections}
    <div class="approval"><strong>إثبات المصادقة الإلكترونية</strong>
      <div>المصادق: <b>{e(report.reviewed_by)}</b></div>
      <div>وقت المصادقة: <b>{e(issued)}</b></div>
      <div>حالة التقرير: معتمد ومقفل ضد التعديل العادي</div></div>
    <footer><div>توقيع منسق المركز:<p>..................................</p></div>
      <div>ختم الجمعية:<p>..................................</p></div>
      <div class="qr">{code}<div><small>رمز تحقق إلكتروني</small></div>
        <small><a href="{e(verify_url)}">التحقق من سجل الاعتماد</a></small></div>
    </footer></article></body></html>'''
    return HTMLResponse(html)
