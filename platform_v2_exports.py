"""Printable individual / batch transcripts and privacy-preserving QR verification.

The browser's Print > Save to PDF creates a single PDF with one transcript per
page. QR verification never discloses a learner's name or grade publicly.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
from datetime import date

import segno
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (actor, assert_csrf, e, page, form, choose, center_options,
                         tracks_options, require_role, transcript, table, center_allowed, log)
from platform_v2_models import Assessment, Center, DocumentProof, Enrollment, Grade, Learner, Track, Year

BASE_URL = os.environ.get("PUBLIC_BASE_URL","https://noor-alamal-python-reports.vercel.app").rstrip("/")

def canonical_fingerprint(db:Session,en:Enrollment,start:date|None,end:date|None):
    l=db.get(Learner,en.learner_id)
    assessments=db.scalars(select(Assessment).where(Assessment.track_id==en.track_id).order_by(Assessment.id)).all()
    items=[]
    for a in assessments:
        if start and a.held_on<start:continue
        if end and a.held_on>end:continue
        g=db.scalar(select(Grade).where(Grade.assessment_id==a.id,Grade.enrollment_id==en.id,Grade.status=="approved"))
        if g:
            items.append([a.id,a.subject,a.title,a.held_on.isoformat(),str(a.coefficient),str(g.score)])
    canonical=json.dumps([l.full_name,en.year_id,en.center_id,en.track_id,items],ensure_ascii=False,separators=(",",":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

def qr_svg(url:str):
    qr=segno.make(url,error="m")
    return qr.svg_inline(scale=2,border=2)

@app.get("/platform/exports/grades",response_class=HTMLResponse)
def grades_export_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator")
    years=db.scalars(select(Year).order_by(Year.starts_on.desc())).all()
    htmlform=form("/platform/exports/grades/print",request.session["csrf"],
      choose("year_id","السنة",[(y.id,y.label) for y in years])+
      choose("center_id","المركز",center_options(db,user))+
      choose("track_id","المسلك",[(0,"كل المسالك")]+tracks_options(db,user))+
      '<label>من (اختياري)<input name="start" type="date"></label>'+
      '<label>إلى (اختياري)<input name="end" type="date"></label>',
      "إنشاء كشف جماعي قابل للحفظ بصيغة PDF")
    return page(request,user,"تصدير كشوف النقط",f'<section><h2>تصدير جماعي أو فردي</h2><p>اختر السنة والمركز والمسلك والفترة. بعد فتح الوثيقة اختر «طباعة» ثم «حفظ بصيغة PDF» لإنشاء ملف واحد لجميع المستفيدين، كل مستفيد في صفحة مستقلة.</p>{htmlform}</section>')

@app.post("/platform/exports/grades/print",response_class=HTMLResponse)
def export_grade_batch(request:Request,token:str=Form(alias="_csrf"),year_id:int=Form(),
       center_id:int=Form(),track_id:int=Form(0),start:date|None=Form(None),end:date|None=Form(None),
       db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator");assert_csrf(request,token)
    if not center_allowed(user,center_id) or (start and end and start>end):
        raise HTTPException(403,"المركز أو الفترة غير مصرح به")
    year=db.get(Year,year_id);center=db.get(Center,center_id)
    if not year or not center:raise HTTPException(422,"السنة أو المركز غير صالحين")
    track=db.get(Track,track_id) if track_id else None
    if track and (track.center_id!=center_id or track.year_id!=year_id):
        raise HTTPException(403,"المسلك لا يتبع هذه السنة والمركز")
    query=select(Enrollment).where(Enrollment.year_id==year_id,Enrollment.center_id==center_id)
    if track:query=query.where(Enrollment.track_id==track.id)
    enrollments=db.scalars(query.order_by(Enrollment.id).limit(700)).all()
    pages=[]
    for en in enrollments:
        learner=db.get(Learner,en.learner_id)
        averages=transcript(db,en,start,end)
        fingerprint=canonical_fingerprint(db,en,start,end)
        token_value=secrets.token_urlsafe(36)
        proof=DocumentProof(token_hash=hashlib.sha256(token_value.encode()).hexdigest(),
            kind="grades",enrollment_id=en.id,fingerprint=fingerprint,period_start=start,period_end=end)
        db.add(proof)
        verify_url=f"{BASE_URL}/verify/{token_value}"
        code=qr_svg(verify_url)
        rows=table(["المادة / المجال","المعدل من 20"],[
            [e(name),e(value) if value is not None else "— (غير مكتمل)"] for name,value in averages.items()
        ])
        period=f"من {start or 'بداية السنة'} إلى {end or 'نهاية السنة'}"
        pages.append(f'''<article class="sheet"><header><img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل" width="100" height="88" style="display:block;margin:auto auto 6px"><div class="logo-text">جمعية نور الأمل</div>
          <p>برنامج الفرصة الثانية – الجيل الجديد</p><h2>كشف النقط الفردي</h2></header>
          <div class="meta"><p>المركز: <b>{e(center.name)}</b></p><p>المسلك: <b>{e(db.get(Track,en.track_id).name)}</b></p>
          <p>السنة التكوينية: <b>{e(year.label)}</b></p><p>الفترة: {e(period)}</p>
          <p>اسم المستفيد(ة): <b>{e(learner.full_name)}</b></p></div>
          {rows}<p class="note">المعدلات من النقط المصادق عليها فقط، دون حساب معدل إجمالي بين التكوين المهني والتأهيل التربوي.</p>
          <footer><div>توقيع منسق المركز<br><br>..........................</div>
          <div>توقيع وختم الجمعية<br><br>..........................</div>
          <div class="qr">{code}<small>تحقق من صحة النسخة<br>رمز QR</small><a href="{e(verify_url)}">التحقق</a></div></footer></article>''')
    if not pages:
        raise HTTPException(404,"لم يُعثر على مستفيدين في هذا المركز والمسلك والسنة")
    db.flush()
    log(db,user,"export_batch","grades",year_id,f"center={center_id};track={track_id};count={len(enrollments)}")
    db.commit()
    stylesheet='''<style>
body{font-family:Tahoma,Arial,sans-serif;color:#192b3d;background:#e9edf2;margin:0;direction:rtl}
.toolbar{background:#124b80;color:white;padding:13px;text-align:center}
.toolbar button{padding:9px 18px;border:0;border-radius:8px;cursor:pointer}
.sheet{box-sizing:border-box;max-width:210mm;min-height:290mm;margin:15px auto;background:white;
 padding:18mm 17mm;page-break-after:always;box-shadow:0 0 7px #888}
header{text-align:center;border-bottom:3px solid #198745;padding-bottom:15px}
.logo-text{color:#1464a1;font-size:24px;font-weight:700}h2{color:#156b43}
.meta{line-height:1.6} .table-scroll{width:100%;overflow:auto}table{width:100%;border-collapse:collapse;margin-top:20px}
th,td{border:1px solid #c9d6df;padding:12px;text-align:right}th{background:#ecf3f8}
.note{font-size:12px;color:#53687b;margin-top:20px}
footer{display:flex;justify-content:space-between;align-items:end;gap:15px;margin-top:45px}
.qr{display:flex;align-items:center;flex-direction:column;font-size:10px;gap:6px}
.qr svg{width:90px;height:90px}
@media print{@page{size:A4;margin:0}.toolbar{display:none}body{background:white}
.sheet{box-shadow:none;margin:0;min-height:297mm;page-break-after:always}}
</style>'''
    document=f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>كشوف النقط — {e(year.label)}</title>
{stylesheet}</head><body><div class="toolbar"><button type="button" onclick="window.print()">طباعة / حفظ PDF مجمع ({len(pages)} مستفيد)</button></div>
{"".join(pages)}</body></html>'''
    return HTMLResponse(document)

@app.get("/verify/{token_value}",response_class=HTMLResponse)
def verify_document(token_value:str,db:Session=Depends(get_db)):
    if len(token_value)<30 or len(token_value)>140:
        raise HTTPException(404,"الوثيقة غير معروفة")
    sha=hashlib.sha256(token_value.encode()).hexdigest()
    record=db.scalar(select(DocumentProof).where(DocumentProof.token_hash==sha))
    valid=False
    if record and record.kind=="grades":
        enrollment=db.get(Enrollment,record.enrollment_id)
        if enrollment:
            current=canonical_fingerprint(db,enrollment,record.period_start,record.period_end)
            valid=secrets.compare_digest(current,record.fingerprint)
    state="الوثيقة مطابقة للبيانات الرسمية المعتمدة" if valid else "تعذر التحقق: الوثيقة غير صالحة أو تغيرت بياناتها"
    fg="#177d43" if valid else "#b02b38"
    return HTMLResponse(f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow"><title>التحقق من وثيقة جمعية نور الأمل</title></head>
<body style="font-family:Tahoma,Arial;text-align:center;padding:50px"><h1>جمعية نور الأمل</h1>
<h2 style="color:{fg}">{e(state)}</h2><p>هذه الصفحة تعرض حالة المطابقة فقط، ولا تنشر هوية المستفيد أو نقطه.</p>
</body></html>''')
