"""Excel/CSV learner import with server-verified previews and partial row acceptance."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import APP_SECRET, app, get_db, make_csv
from platform_v2 import (actor, assert_csrf, center_allowed, e, page, form, choose,
                         tracks_options, require_role, log, field, table, now)
from platform_v2_models import Enrollment, Learner, Track, Year

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1500

def normalize(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

def cols(row):
    return {normalize(k): v for k,v in row.items() if k is not None}

def column(row, *names):
    for name in names:
        value=row.get(normalize(name))
        if value is not None and str(value).strip():
            return value
    return ""

def parse_date(raw):
    if raw is None or str(raw).strip()=="":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    value=str(raw).strip()
    for pattern in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y","%d.%m.%Y"):
        try:return datetime.strptime(value,pattern).date()
        except ValueError:pass
    found=re.search(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{1,2}-\d{1,2})",value)
    if found:return parse_date(found.group(1))
    raise ValueError("تاريخ الازدياد غير مفهوم")

def clean_row(raw):
    r=cols(raw)
    full_name=str(column(r,"الاسم الكامل","اسم المستفيد","الاسم والنسب","full name","full_name")).strip()
    if not full_name:
        first=str(column(r,"الاسم","prenom","prénom","first name")).strip()
        family=str(column(r,"النسب","اللقب","nom","last name")).strip()
        full_name=" ".join(x for x in (first,family) if x)
    dob_field=column(r,"تاريخ الازدياد","تاريخ الميلاد","تاريخ الازدياد والمكان","date de naissance","birthdate")
    mixed=column(r,"تاريخ ومكان الازدياد")
    if not dob_field and mixed:dob_field=mixed
    born_on=parse_date(dob_field)
    place=str(column(r,"مكان الازدياد","مكان الميلاد","birthplace")).strip()
    if not place and mixed:
        place=re.sub(r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{4}-\d{1,2}-\d{1,2})","",str(mixed)).strip(" ,،-")
    gender=str(column(r,"الجنس","النوع","gender","sexe")).strip()
    guardian_name=str(column(r,"اسم ولي الأمر","اسم الوصي","ولي الأمر")).strip()
    guardian_phone=str(column(r,"هاتف ولي الأمر","هاتف الوصي","guardian phone")).strip()
    record=dict(full_name=full_name,gender=gender,born_on=born_on.isoformat() if born_on else None,
       birthplace=place,phone=str(column(r,"الهاتف","هاتف المستفيد","phone")).strip(),
       address=str(column(r,"العنوان","address")).strip(),
       guardian_name=guardian_name,guardian_relationship=str(column(r,"صلة القرابة","الصفة")).strip(),
       guardian_phone=guardian_phone)
    if len(full_name)<3 or len(full_name)>180:raise ValueError("الاسم الكامل غير صالح")
    if full_name[:1] in ("=","+","-","@"):raise ValueError("صيغة غير مسموح بها في الاسم")
    if gender not in ("","ذكر","أنثى","M","F","m","f"):raise ValueError("الجنس غير معروف")
    if born_on and born_on>date.today():raise ValueError("تاريخ الميلاد في المستقبل")
    if born_on and (date.today()-born_on).days<18*365 and not guardian_phone:
        raise ValueError("هاتف ولي الأمر إلزامي للقاصر")
    if any(len(str(v or ""))>240 for v in record.values()):raise ValueError("حقل أطول من الحجم المسموح")
    return record

def parse_upload(filename, data):
    suffix=Path(filename or "").suffix.lower()
    if suffix not in (".xlsx",".csv"):raise HTTPException(422,"تُقبل ملفات XLSX أو CSV فقط")
    if not data or len(data)>MAX_UPLOAD_BYTES:raise HTTPException(413,"الملف فارغ أو تجاوز 5 ميغابايت")
    if suffix==".xlsx":
        try:
            wb=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
            ws=wb.active
            it=ws.iter_rows(values_only=True)
            header=[str(x or "").strip() for x in next(it)]
            rows=({header[i]:v for i,v in enumerate(values) if i<len(header)} for values in it)
            output=[]
            for idx,record in enumerate(rows,start=2):
                if idx>MAX_ROWS+1:raise HTTPException(413,"الملف يحتوي أكثر من 1500 صف")
                if any(v is not None and str(v).strip() for v in record.values()):output.append((idx,record))
            wb.close()
            return output
        except HTTPException:raise
        except Exception as exc:raise HTTPException(422,"ملف Excel غير صالح أو تالف") from exc
    try:
        try:text=data.decode("utf-8-sig")
        except UnicodeDecodeError:text=data.decode("cp1256")
        reader=csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:raise ValueError
        result=[]
        for idx,row in enumerate(reader,start=2):
            if idx>MAX_ROWS+1:raise HTTPException(413,"الملف يحتوي أكثر من 1500 صف")
            if any(str(v or "").strip() for v in row.values()):result.append((idx,row))
        return result
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,"ملف CSV غير صالح") from exc

def duplicates(db:Session,record,year_id):
    existing=db.scalars(select(Learner).where(Learner.full_name==record["full_name"],
              Learner.born_on==date.fromisoformat(record["born_on"]) if record["born_on"] else Learner.born_on.is_(None))).all()
    if len(existing)>1:return "اسم وتاريخ ميلاد متطابقان في أكثر من ملف؛ يلزم التدقيق"
    if len(existing)==1:
        enrolled=db.scalar(select(Enrollment).where(Enrollment.learner_id==existing[0].id,Enrollment.year_id==year_id))
        if enrolled:return "المستفيد مسجل مسبقًا في هذه السنة"
    return ""

def token_for(payload):
    return hmac.new(APP_SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()

@app.get("/platform/import",response_class=HTMLResponse)
def import_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator")
    tracks=tracks_options(db,user)
    options=choose("track_id","المسلك والسنة",tracks)
    formhtml=f'''<form action="/platform/import/preview" method="post" enctype="multipart/form-data" class="form-grid">
        <input type="hidden" name="_csrf" value="{e(request.session["csrf"])}">{options}
        <label>ملف المستفيدين (.xlsx أو .csv) <input type="file" name="file" accept=".xlsx,.csv" required></label>
        <button type="submit">معاينة البيانات والأخطاء</button></form>'''
    body='<section><h2>استيراد دفعي مع معاينة قبل التسجيل</h2><p>يمكن استعمال عمود «الاسم الكامل»، أو عمودي «الاسم» و«النسب»، و«تاريخ الازدياد» و«هاتف ولي الأمر» للقاصرين.</p>'+formhtml+'</section>'
    return page(request,user,"استيراد Excel وCSV",body)

@app.post("/platform/import/preview",response_class=HTMLResponse)
async def import_preview(request:Request,token:str=Form(alias="_csrf"),track_id:int=Form(),
      file:UploadFile=File(...),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator");assert_csrf(request,token)
    track=db.get(Track,track_id)
    if not track or not center_allowed(user,track.center_id):raise HTTPException(403)
    data=await file.read(MAX_UPLOAD_BYTES+1)
    source=parse_upload(file.filename,data)
    valid=[];invalid=[];seen=set()
    for idx,record in source:
        try:
            row=clean_row(record)
            key=(normalize(row["full_name"]),row["born_on"])
            if key in seen:raise ValueError("مكرر داخل الملف")
            seen.add(key)
            issue=duplicates(db,row,track.year_id)
            if issue:raise ValueError(issue)
            valid.append(row)
        except (ValueError,KeyError) as exc:
            invalid.append((idx,str(exc)[:180]))
    payload=json.dumps({"track_id":track_id,"rows":valid},ensure_ascii=False,separators=(",",":"))
    if len(payload)>600000:raise HTTPException(413,"عدد البيانات أكبر من الحد الآمن للمعاينة")
    signature=token_for(payload)
    preview=table(["الاسم","تاريخ الازدياد","النوع"],[[e(r["full_name"]),e(r["born_on"] or "—"),e(r["gender"])] for r in valid[:60]])
    errors=table(["رقم السطر","الخطأ"],[[e(n),e(err)] for n,err in invalid[:200]])
    reject_csv=io.StringIO();writer=csv.writer(reject_csv);writer.writerow(["رقم السطر","سبب الرفض"]);writer.writerows(invalid)
    encoded=quote("\ufeff"+reject_csv.getvalue())
    commit=form("/platform/import/commit",request.session["csrf"],
         f'<input type="hidden" name="payload" value="{e(payload)}"><input type="hidden" name="signature" value="{signature}">',
         f"استيراد {len(valid)} صفًا صالحًا") if valid else "<p>لا توجد صفوف صالحة.</p>"
    return page(request,user,"معاينة الاستيراد",f'<section><p>صفوف صالحة: {len(valid)} · مرفوضة: {len(invalid)}</p>{preview}{commit}</section><section><h2>أخطاء الاستيراد</h2>{errors}<a class="button" download="import-errors.csv" href="data:text/csv;charset=utf-8,{encoded}">تنزيل ملف الأخطاء CSV</a></section>')

@app.post("/platform/import/commit",response_class=HTMLResponse)
def import_commit(request:Request,token:str=Form(alias="_csrf"),payload:str=Form(),signature:str=Form(),
       db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator");assert_csrf(request,token)
    if len(payload)>600000 or not hmac.compare_digest(signature,token_for(payload)):
        raise HTTPException(403,"توقيع المعاينة غير صالح؛ أعد رفع الملف")
    try:
        parsed=json.loads(payload)
        track=db.get(Track,int(parsed["track_id"]))
        rows=parsed["rows"]
    except (ValueError,KeyError,TypeError):raise HTTPException(422,"بيانات المعاينة غير صحيحة")
    if not track or not center_allowed(user,track.center_id) or not isinstance(rows,list) or len(rows)>MAX_ROWS:
        raise HTTPException(403)
    ok=0;errors=[]
    for index,source in enumerate(rows,start=1):
        try:
            record=clean_row(source)
            issue=duplicates(db,record,track.year_id)
            if issue:raise ValueError(issue)
            birth=date.fromisoformat(record["born_on"]) if record["born_on"] else None
            match=db.scalar(select(Learner).where(Learner.full_name==record["full_name"],Learner.born_on==birth))
            if match:learner=match
            else:
                learner=Learner(**{**record,"born_on":birth})
                db.add(learner);db.flush()
            enrolled=Enrollment(learner_id=learner.id,year_id=track.year_id,
                center_id=track.center_id,track_id=track.id)
            db.add(enrolled);db.flush()
            log(db,user,"bulk_enroll","enrollment",enrolled.id)
            db.commit();ok+=1
        except (ValueError,IntegrityError) as exc:
            db.rollback();errors.append((index,"تكرار أو بيانات غير صحيحة"))
    body=f'<section><h2>نتيجة الاستيراد</h2><p>تم إدخال {ok} مستفيدًا؛ فشل {len(errors)} صفًا.</p><a class="button" href="/platform/learners">الانتقال لملفات المستفيدين</a></section>'
    if errors:body+='<section>'+table(["رقم الصف المقبول في المعاينة","النتيجة"],[[e(i),e(t)] for i,t in errors])+'</section>'
    return page(request,user,"نتيجة الاستيراد الجزئي",body)

@app.get("/platform/import/template.csv")
def import_template(user=Depends(actor)):
    require_role(user,"admin","coordinator")
    return make_csv("noor-learners-template.csv",["الاسم الكامل","الجنس","تاريخ الازدياد",
        "مكان الازدياد","هاتف ولي الأمر","اسم ولي الأمر","صلة القرابة","العنوان"],
        [["مستفيد تجريبي","ذكر","2010-05-10","الرباط","0600000000","ولي تجريبي","الأب",""]])
