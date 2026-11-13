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


def report_metadata_table(year, track, slot, session, instructor, present: int, absent: int) -> str:
    """Compact, readable two-column data table for A4 printing."""
    entries = (
        ("السنة التكوينية", year.label, "المركز / المسلك", track.name),
        ("المادة", slot.subject, "تاريخ الحصة", session.held_on),
        ("المؤطر(ة)", instructor.full_name, "التوقيت", f"{slot.starts_at}–{slot.ends_at}"),
        ("عدد الحاضرين", present, "عدد الغائبين", absent),
    )
    rows = "".join(
        f'<tr><th scope="row">{e(a)}</th><td>{e(b)}</td>'
        f'<th scope="row">{e(c)}</th><td>{e(d)}</td></tr>'
        for a, b, c, d in entries
    )
    return (
        '<table class="report-metadata" aria-label="بيانات الحصة">'
        '<colgroup><col class="meta-label"><col class="meta-value">'
        '<col class="meta-label"><col class="meta-value"></colgroup>'
        '<tbody>' + rows + '</tbody></table>'
    )


def report_content_tables(fields) -> str:
    """Separate bordered tables allow long Arabic text to flow onto later pages."""
    blocks = []
    for index, (label, body) in enumerate(fields, 1):
        value = e(body) if body else "لا توجد ملاحظات"
        blocks.append(
            '<table class="report-content-table">'
            '<thead><tr><th scope="col">'
            f'<span class="section-index">{index:02d}</span> {e(label)}'
            '</th></tr></thead>'
            f'<tbody><tr><td><div class="report-text">{value}</div></td></tr></tbody>'
            '</table>'
        )
    return '<div class="report-content" aria-label="محتوى التقرير">' + "".join(blocks) + '</div>'


PRINT_LAYOUT_CSS = """
@page { size: A4 portrait; margin: 12mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: Tahoma, Arial, sans-serif; line-height: 1.65; }
.sheet { width: 100%; max-width: 210mm; min-height: 0; padding: 13mm 13mm;
         border-radius: 4px; }
.sheet header { gap: 14px; }
.sheet header img { width: 76px; height: 72px; flex-shrink: 0; }
.sheet h1 { font-size: 22px; }
.sheet h2 { margin: 20px 0 12px; line-height: 1.55; font-size: 18px; }
.report-title { font-size: 15px; line-height: 1.65; padding: 11px 13px;
                border: 1px solid #cbd8e2; background: #f6f9fb;
                margin: 16px 0; overflow-wrap: anywhere; }
.report-title strong { color: #254d69; }
.report-metadata, .report-content-table { width: 100%; border-collapse: collapse;
  table-layout: fixed; direction: rtl; font-size: 12px; }
.report-metadata { margin: 14px 0 18px; }
.report-metadata .meta-label { width: 18%; }
.report-metadata .meta-value { width: 32%; }
.report-metadata th, .report-metadata td { border: 1px solid #c9d5e0;
  padding: 8px 9px; text-align: right; vertical-align: top; overflow-wrap: anywhere; }
.report-metadata th { background: #edf3f7; font-weight: 700; color: #24475f; }
.report-metadata td { color: #193145; }
.report-content-table { margin: 13px 0; page-break-inside: auto; break-inside: auto; }
.report-content-table thead { display: table-header-group; }
.report-content-table th { background: #eaf1f6; color: #234c68; border: 1px solid #bfcfdc;
  text-align: right; padding: 8px 12px; font-size: 13px; }
.report-content-table td { border: 1px solid #bfcfdc; padding: 11px 13px;
  vertical-align: top; }
.report-text { white-space: pre-wrap; overflow-wrap: anywhere; word-break: normal;
  line-height: 1.85; font-size: 12.5px; text-align: right; min-height: 17px; }
.section-index { display: inline-block; margin-left: 9px; font-size: 11px; }
.approval { margin-top: 18px; }
.notice { margin-top: 20px; }
footer { margin-top: 22px; align-items: flex-start; }
.qr { text-align: center; }
.qr a { font-size: 10px; overflow-wrap: anywhere; }
@media print {
  @page { size: A4 portrait; margin: 12mm; }
  body { background: #fff; }
  .toolbar { display: none !important; }
  .sheet { max-width: none; width: auto; margin: 0; padding: 0; min-height: 0;
           box-shadow: none; border: 0; }
  .report-content-table, .report-metadata { print-color-adjust: exact;
           -webkit-print-color-adjust: exact; }
  .report-content-table tr { break-inside: auto; page-break-inside: auto; }
  .report-content-table thead tr { break-inside: avoid; }
  .report-metadata tr, .sheet header { break-inside: avoid; page-break-inside: avoid; }
}
"""

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
    sections = report_content_tables(fields)
    metadata = report_metadata_table(year, track, slot, session, instructor, present, absent)
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
    {PRINT_LAYOUT_CSS}</style></head><body>
    <div class="toolbar"><button type="button" onclick="window.print()">طباعة النسخة الأولية / حفظ PDF</button></div>
    <article class="sheet"><header><div><h1>جمعية نور الأمل</h1>
      <div>برنامج الفرصة الثانية – الجيل الجديد</div>
      <div>{e(center.name)}</div></div>
      <img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل"></header>
    <div class="warning" role="note">نسخة أولية غير معتمدة — ليست وثيقة رسمية</div>
    <h2>تقرير حصة أولي · رقم {report.id}</h2>
    <p class="status">الحالة: {e(label)}</p>
    {metadata}
    <div class="report-title"><strong>عنوان الحصة:</strong> {e(report.subject)}</div>
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
    sections = report_content_tables(fields)
    metadata = report_metadata_table(year, track, slot, session, instructor, present, absent)
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
    {PRINT_LAYOUT_CSS}</style></head><body>
    <div class="toolbar"><button type="button" onclick="window.print()">طباعة / حفظ PDF الرسمي</button></div>
    <article class="sheet"><header><div><h1>جمعية نور الأمل</h1>
      <div>برنامج الفرصة الثانية – الجيل الجديد</div><div>{e(center.name)}</div></div>
      <img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل"></header>
    <h2>تقرير حصة معتمد إلكترونيًا · رقم {report.id}</h2>
    {metadata}
    <div class="report-title"><strong>عنوان الحصة:</strong> {e(report.subject)}</div>
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
