"""Employee-owned professional card details. Private and staff-authenticated."""
from __future__ import annotations

import io
import re

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, e, page, redirect
from platform_v2_models import Audit, StaffProfile, now

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 8_000_000
PHOTO_SIZE = (440, 520)


def professional_profile(user: dict) -> int:
    """Personal edits are restricted to the authenticated staff member."""
    if user["role"] not in ("coordinator", "instructor", "educator") or not isinstance(user.get("id"), int):
        raise HTTPException(403, "الملف المهني مخصص لحسابات العاملين بالجمعية.")
    return user["id"]


def normalized_image(photo_bytes: bytes) -> bytes:
    """Reject fake/oversized media, remove EXIF, convert to safe bounded JPEG."""
    if len(photo_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "الصورة أكبر من 2 ميغابايت.")
    try:
        with Image.open(io.BytesIO(photo_bytes)) as verify:
            if verify.format not in ("JPEG", "PNG"):
                raise HTTPException(422, "الصور المقبولة JPG أو PNG فقط.")
            if verify.width * verify.height > MAX_IMAGE_PIXELS:
                raise HTTPException(413, "أبعاد الصورة كبيرة جدًا.")
            verify.verify()
        with Image.open(io.BytesIO(photo_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image.convert("RGB"), PHOTO_SIZE, method=Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=78, optimize=True)
            return buffer.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "الصورة غير صالحة. استعمل JPG أو PNG.")


@app.get("/platform/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    staff_id = professional_profile(user)
    record = db.get(StaffProfile, staff_id)
    specialty = record.specialty if record else ""
    phone = record.work_phone if record else ""
    email = record.work_email if record else ""
    uploaded = bool(record and record.photo_data)
    body = (
        '<section><h2>المعلومات التي ستظهر في البطاقة المهنية</h2>'
        '<p class="subtle">املأ ما يناسبك فقط. الاسم والصفة والمركز تُحددها الإدارة، '
        'أما المعلومات أدناه فيمكنك تعديلها بنفسك. الصورة اختيارية ويستطيع المسؤول طباعتها على البطاقة.</p>'
        '<form method="post" action="/platform/profile" enctype="multipart/form-data" class="form-grid">'
        f'<input type="hidden" name="_csrf" value="{e(request.session["csrf"])}">'
        f'<label>التخصص أو المهمة المهنية<input name="specialty" type="text" maxlength="100" value="{e(specialty)}" placeholder="مثال: التنشيط التربوي"></label>'
        f'<label>رقم الهاتف المهني<input name="work_phone" type="tel" maxlength="35" value="{e(phone)}" placeholder="+212..."></label>'
        f'<label>البريد الإلكتروني المهني<input name="work_email" type="email" maxlength="150" value="{e(email)}" placeholder="name@example.org"></label>'
        '<label>صورة شخصية اختيارية (JPG أو PNG، بحد أقصى 2MB)'
        '<input name="photo" type="file" accept="image/jpeg,image/png"></label>'
        f'<label style="flex:0 0 auto"><input name="remove_photo" type="checkbox" value="yes"> حذف الصورة الحالية'
        f'{" (توجد صورة محفوظة)" if uploaded else ""}</label>'
        '<button type="submit">حفظ معلومات بطاقتي</button></form>'
        '<p class="subtle">لا تدخل رقم البطاقة الوطنية أو العنوان الشخصي. المعلومات المسجلة ستظهر عند طباعة البطاقة.</p>'
        '</section>'
        '<section><a class="button" href="/platform/my-card">معاينة بطاقتي المهنية</a></section>')
    return page(request, user, "معلومات بطاقتي المهنية", body)


@app.post("/platform/profile")
async def save_profile(request: Request, token: str = Form(alias="_csrf"),
                       specialty: str = Form(""), work_phone: str = Form(""), work_email: str = Form(""),
                       remove_photo: str = Form(""), photo: UploadFile | None = File(None),
                       db: Session = Depends(get_db), user=Depends(actor)):
    staff_id = professional_profile(user)
    assert_csrf(request, token)
    specialty, work_phone, work_email = specialty.strip(), work_phone.strip(), work_email.strip()
    if (len(specialty) > 100 or len(work_phone) > 35 or len(work_email) > 150 or
        (work_phone and not re.fullmatch(r"[0-9+() .-]+", work_phone)) or
        (work_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", work_email))):
        raise HTTPException(422, "يرجى مراجعة التخصص أو رقم الهاتف أو البريد الإلكتروني.")
    new_image = None
    if photo and photo.filename:
        if photo.content_type not in ("image/jpeg", "image/png"):
            raise HTTPException(422, "يُسمح فقط بصور JPG أو PNG.")
        raw = await photo.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "الصورة أكبر من 2 ميغابايت.")
        new_image = normalized_image(raw)
    record = db.get(StaffProfile, staff_id)
    if record is None:
        record = StaffProfile(staff_id=staff_id)
        db.add(record)
    record.specialty = specialty
    record.work_phone = work_phone
    record.work_email = work_email
    if remove_photo == "yes":
        record.photo_data = None
        record.photo_mime = ""
    elif new_image is not None:
        record.photo_data = new_image
        record.photo_mime = "image/jpeg"
    record.updated_at = now()
    # Audit against a stable non-personal actor identifier; only staff control own data.
    db.add(Audit(actor=f"موظف رقم {staff_id}", action="staff_profile_update",
                 resource="staff_profile", resource_id=staff_id, reason="تحديث بيانات البطاقة المهنية"))
    db.commit()
    return redirect("/platform/profile")


@app.get("/platform/my-card", response_class=HTMLResponse)
def own_staff_card(db: Session = Depends(get_db), user=Depends(actor)):
    staff_id = professional_profile(user)
    from platform_v2 import staff_cards_html
    from platform_v2_models import Staff
    staff = db.get(Staff, staff_id)
    if staff is None or not staff.enabled:
        raise HTTPException(403, "الحساب غير نشط")
    return staff_cards_html([staff], db, back_to="/platform/profile")
