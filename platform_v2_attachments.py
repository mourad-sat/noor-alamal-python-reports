"""Role-scoped evidence attachments kept out of public URLs.

Storage is private database BYTEA for the initial installation; monitor free
database quota before allowing 50 MB per report over many sessions.
"""
from __future__ import annotations

import io
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (actor, assert_csrf, center_allowed, coordinator, e, form,
                        page, require_role, table, log)
from platform_v2_models import Attachment, AttachmentBlob, SessionReport, SessionDay, Slot, Track

MAX_IMAGE=5*1024*1024
MAX_DOC=20*1024*1024
MAX_TOTAL=50*1024*1024
ALLOWED={".pdf":"application/pdf",".jpg":"image/jpeg",".jpeg":"image/jpeg",
         ".png":"image/png",".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

def report_access(db:Session,user,report_id:int):
    report=db.get(SessionReport,report_id)
    if not report:raise HTTPException(404)
    session=db.get(SessionDay,report.session_id)
    slot=db.get(Slot,session.slot_id)
    if user["role"]=="admin" or (user["role"]=="coordinator" and center_allowed(user,slot.center_id)):
        return report,slot
    if (user["role"] in ("instructor", "educator")
            and user["id"]==report.author_id and user["id"]==slot.staff_id
            and any(a.center_id==slot.center_id and a.track_id==slot.track_id
                    for a in user["assignments"])):
        return report,slot
    raise HTTPException(403,"مرفقات التقرير خارج نطاق صلاحياتك")

def attachment_access(db,user,attachment_id:int):
    attachment=db.get(Attachment,attachment_id)
    if not attachment:raise HTTPException(404)
    report,slot=report_access(db,user,attachment.report_id)
    return attachment,report,slot

def validate_bytes(suffix:str,data:bytes):
    if suffix==".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(422,"ملف PDF غير صالح")
    if suffix==".png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422,"الصورة PNG غير صالحة")
    if suffix in (".jpg",".jpeg") and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(422,"الصورة JPEG غير صالحة")
    if suffix in (".docx",".xlsx") and not data.startswith(b"PK\x03\x04"):
        raise HTTPException(422,"ملف Office غير صالح")

@app.get("/platform/reports/{report_id}/attachments")
def attachments_page(report_id:int,request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    report,slot=report_access(db,user,report_id)
    csrf=request.session["csrf"]
    attachments=db.scalars(select(Attachment).where(Attachment.report_id==report.id).order_by(Attachment.id)).all()
    rows=[]
    for item in attachments:
        link=f'<a href="/platform/attachments/{item.id}/download">{e(item.original_name)}</a>'
        summary="نعم" if item.select_for_summary else "لا"
        action=""
        if user["role"] in ("admin", "coordinator") and report.status=="approved":
            action=form(f"/platform/attachments/{item.id}/select",csrf,"","إلغاء الاختيار" if item.select_for_summary else "إدراج بالتقرير الدوري")
        if user["role"]=="admin":
            action+=form(f"/platform/attachments/{item.id}/delete",csrf,
                '<input name="confirmation" value="" placeholder="اكتب: حذف المرفق" required>'
                + __import__("platform_v2").area("reason","سبب حذف المرفق (إلزامي)"),
                "حذف المرفق نهائيًا")
        rows.append([link,e(item.content_type),e(round(item.byte_size/1048576,2)),e(summary),action])
    body=f'<section><h2>مرفقات التقرير رقم {report.id}</h2><p>المرفقات سرية. لا تدخل تلقائيًا في التقارير الدورية؛ المنسق يحددها يدويًا بعد المصادقة على تقرير الحصة.</p>{table(["الملف","الصيغة","الحجم MB","للتقرير الدوري","اختيار المنسق"],rows)}</section>'
    if report.status in ("draft","returned") and user["id"]==report.author_id:
        body+='<section><h2>إضافة صورة أو وثيقة</h2><p>الصورة حتى 5 MB، الوثيقة حتى 20 MB، مجموع التقرير حتى 50 MB. الصيغ: PDF، JPG، PNG، DOCX، XLSX.</p>'+\
            f'<form method="post" action="/platform/reports/{report.id}/attachments" enctype="multipart/form-data" class="form-grid">'+\
            f'<input type="hidden" name="_csrf" value="{e(csrf)}"><input type="file" name="file" required accept=".pdf,.jpg,.jpeg,.png,.docx,.xlsx"><button>رفع الملف</button></form></section>'
    return page(request,user,"مرفقات التقرير",body)

@app.post("/platform/reports/{report_id}/attachments")
async def upload_attachment(report_id:int,request:Request,token:str=Form(alias="_csrf"),
          file:UploadFile=File(...),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    report,slot=report_access(db,user,report_id)
    if user["id"]!=report.author_id or report.status not in ("draft","returned"):
        raise HTTPException(403,"التقرير المعتمد أو المرسل مقفل")
    original_name=Path(file.filename or "").name
    if not original_name or len(original_name)>250:raise HTTPException(422,"اسم الملف غير صالح")
    suffix=Path(original_name).suffix.lower()
    if suffix not in ALLOWED:raise HTTPException(422,"صيغة المرفق غير مسموح بها")
    limit=MAX_IMAGE if suffix in (".jpg",".jpeg",".png") else MAX_DOC
    contents=await file.read(limit+1)
    if not contents or len(contents)>limit:raise HTTPException(413,"حجم المرفق تجاوز الحد المسموح")
    validate_bytes(suffix,contents)
    current=db.scalar(select(func.coalesce(func.sum(Attachment.byte_size),0)).where(Attachment.report_id==report.id)) or 0
    if current+len(contents)>MAX_TOTAL:raise HTTPException(413,"تجاوز مجموع المرفقات 50 ميغابايت")
    item=Attachment(report_id=report.id,storage_key=secrets.token_urlsafe(32),
          original_name=original_name,content_type=ALLOWED[suffix],byte_size=len(contents))
    db.add(item);db.flush()
    db.add(AttachmentBlob(attachment_id=item.id,content=contents))
    log(db,user,"attachment_upload","attachment",item.id)
    db.commit()
    return RedirectResponse(f"/platform/reports/{report.id}/attachments",status_code=303)

@app.post("/platform/attachments/{attachment_id}/select")
def select_attachment(attachment_id:int,request:Request,token:str=Form(alias="_csrf"),
         db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    item,report,slot=attachment_access(db,user,attachment_id)
    coordinator(user,slot.center_id)
    if report.status!="approved":raise HTTPException(409,"يجب اعتماد تقرير الحصة قبل اختيار مرفقاته")
    item.select_for_summary=not item.select_for_summary
    log(db,user,"attachment_select","attachment",item.id,str(item.select_for_summary))
    db.commit()
    return RedirectResponse(f"/platform/reports/{report.id}/attachments",status_code=303)

def load_attachment(db,item:Attachment):
    blob=db.scalar(select(AttachmentBlob).where(AttachmentBlob.attachment_id==item.id))
    if blob is None:raise HTTPException(404,"تعذر العثور على الملف")
    return blob.content

@app.get("/platform/attachments/{attachment_id}/download")
def download_attachment(attachment_id:int,db:Session=Depends(get_db),user=Depends(actor)):
    item,report,slot=attachment_access(db,user,attachment_id)
    name=quote(re.sub(r'[\\/\r\n]','_',item.original_name),safe="")
    contents=load_attachment(db,item)
    return Response(content=contents,media_type="application/octet-stream",
          headers={"Content-Disposition":f"attachment; filename*=UTF-8''{name}",
                   "Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

@app.get("/platform/attachments/{attachment_id}/view")
def view_selected_image(attachment_id:int,db:Session=Depends(get_db),user=Depends(actor)):
    item,report,slot=attachment_access(db,user,attachment_id)
    session=db.get(SessionDay,report.session_id)
    track=db.get(Track,slot.track_id)
    if (not item.select_for_summary or item.content_type not in ("image/png","image/jpeg")
        or report.status!="approved" or session.status=="canceled"
        or slot.canceled or not track
        or track.center_id!=slot.center_id or track.year_id!=slot.year_id):
        raise HTTPException(403,"الصورة غير معتمدة للعرض")
    return Response(content=load_attachment(db,item),media_type=item.content_type,
        headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                 "Content-Security-Policy":"default-src 'none'; sandbox"})


@app.post("/platform/attachments/{attachment_id}/delete")
def delete_attachment_admin(attachment_id:int,request:Request,
      token:str=Form(alias="_csrf"),reason:str=Form(),confirmation:str=Form(),
      db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    if confirmation.strip()!="حذف المرفق" or len(reason.strip())<10:
        raise HTTPException(422,"تأكيد الحذف وتعليله إلزاميان")
    item,report,slot=attachment_access(db,user,attachment_id)
    blob=db.scalar(select(AttachmentBlob).where(AttachmentBlob.attachment_id==item.id))
    if blob:db.delete(blob)
    db.delete(item)
    import hashlib, hmac
    from app import APP_SECRET
    from platform_v2_models import Audit
    stamp=hmac.new(APP_SECRET.encode(),reason.strip().encode(),hashlib.sha256).hexdigest()[:16]
    # The audit receipt never stores a filename or personal details.
    db.add(Audit(actor=user["name"],action="attachment_erasure",
                 resource="privacy",resource_id=0,reason=f"reason_digest={stamp}"))
    db.commit()
    return RedirectResponse(f"/platform/reports/{report.id}/attachments",status_code=303)
