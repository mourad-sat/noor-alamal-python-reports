"""Private learner portraits and professionally formatted full/small print cards.

Photos are kept in PostgreSQL, not in /static or external cloud storage.
Only admin and the learner's center coordinator may upload/view/print.
"""
from __future__ import annotations

import base64
import io

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (
    actor, assert_csrf, e, learner_scope, log, require_role, redirect
)
from platform_v2_models import Center, Enrollment, Learner, LearnerPhoto, Track, Year, now

MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_PHOTO_PIXELS = 8_000_000
PHOTO_SIZE = (480, 600)


def can_manage_learner(db: Session, user: dict, enrollment_id: int) -> tuple[Enrollment, Learner]:
    require_role(user, "admin", "coordinator")
    enrollment = learner_scope(db, user, enrollment_id)
    learner = db.get(Learner, enrollment.learner_id)
    if learner is None:
        raise HTTPException(404, "ملف المستفيد غير موجود")
    return enrollment, learner


def clean_portrait(contents: bytes) -> bytes:
    """Validate real JPG/PNG/WEBP, cap dimensions and strip EXIF before saving."""
    if not contents or len(contents) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "الصورة فارغة أو تتجاوز 2 ميغابايت.")
    try:
        with Image.open(io.BytesIO(contents)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP"):
                raise HTTPException(422, "الصور المقبولة: JPG أو PNG أو WEBP فقط.")
            if img.width * img.height > MAX_PHOTO_PIXELS:
                raise HTTPException(413, "أبعاد الصورة كبيرة جدًا.")
            img.verify()
        with Image.open(io.BytesIO(contents)) as img:
            img = ImageOps.exif_transpose(img)
            # Resizing and JPEG re-encoding removes EXIF, scripts, and metadata.
            img = ImageOps.fit(img.convert("RGB"), PHOTO_SIZE, method=Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=82, optimize=True)
            return buffer.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "ملف الصورة غير صالح. اختر صورة JPG أو PNG أو WEBP.")


@app.post("/platform/learners/{enrollment_id:int}/photo")
async def upload_learner_photo(
    enrollment_id: int, request: Request, token: str = Form(alias="_csrf"),
    photo: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(actor),
):
    _, learner = can_manage_learner(db, user, enrollment_id)
    assert_csrf(request, token)
    if photo.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(422, "الصورة يجب أن تكون بصيغة JPG أو PNG أو WEBP.")
    contents = await photo.read(MAX_PHOTO_BYTES + 1)
    portrait = clean_portrait(contents)
    existing = db.get(LearnerPhoto, learner.id)
    if existing is None:
        existing = LearnerPhoto(learner_id=learner.id, content=portrait)
        db.add(existing)
    else:
        existing.content = portrait
        existing.updated_at = now()
    log(db, user, "learner_photo_update", "learner_photo", learner.id, "تحديث الصورة المهنية للمستفيد")
    db.commit()
    return redirect(f"/platform/learners/{enrollment_id}")


@app.post("/platform/learners/{enrollment_id:int}/photo/delete")
def delete_learner_photo(
    enrollment_id: int, request: Request, token: str = Form(alias="_csrf"),
    confirmation: str = Form(""), db: Session = Depends(get_db), user=Depends(actor),
):
    _, learner = can_manage_learner(db, user, enrollment_id)
    assert_csrf(request, token)
    if confirmation != "حذف الصورة":
        raise HTTPException(422, "يجب تأكيد حذف الصورة.")
    existing = db.get(LearnerPhoto, learner.id)
    if existing is not None:
        db.delete(existing)
        log(db, user, "learner_photo_delete", "learner_photo", learner.id, "حذف صورة المستفيد")
        db.commit()
    return redirect(f"/platform/learners/{enrollment_id}")


@app.get("/platform/learners/{enrollment_id:int}/photo/file")
def view_learner_photo(
    enrollment_id: int, db: Session = Depends(get_db), user=Depends(actor),
):
    _, learner = can_manage_learner(db, user, enrollment_id)
    portrait = db.get(LearnerPhoto, learner.id)
    if portrait is None:
        raise HTTPException(404, "لم تُرفع صورة لهذا المستفيد")
    return Response(content=portrait.content, media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": "inline"})


def portrait_html(db: Session, learner: Learner) -> str:
    portrait = db.get(LearnerPhoto, learner.id)
    if portrait is None:
        return ('<div class="portrait portrait-empty" role="img" '
                'aria-label="لم تُضف صورة للمستفيد">'
                '<svg viewBox="0 0 80 80" aria-hidden="true">'
                '<circle cx="40" cy="26" r="14" fill="#b9cddd"/>'
                '<path d="M14 72c0-18 11-28 26-28s26 10 26 28" fill="#b9cddd"/>'
                '</svg><span>الصورة غير متوفرة</span></div>')
    data = base64.b64encode(portrait.content).decode("ascii")
    return f'<img class="portrait" src="data:image/jpeg;base64,{data}" alt="صورة المستفيد">'


def info(label: str, value) -> str:
    val = str(value).strip() if value is not None else ""
    return (f'<div class="info"><span class="field-label">{e(label)}</span>'
            f'<span class="field-value">{e(val) if val else "لم تُستكمل بعد"}</span></div>')


def render_card(db: Session, enrollment: Enrollment, learner: Learner, kind: str = "a4") -> HTMLResponse:
    center = db.get(Center, enrollment.center_id)
    track = db.get(Track, enrollment.track_id)
    year = db.get(Year, enrollment.year_id)
    portrait = portrait_html(db, learner)
    if kind == "mini":
        return render_mini_card(enrollment, learner, center, track, year, portrait)
    return render_a4_card(enrollment, learner, center, track, year, portrait)


def toolbar(enrollment_id: int, kind: str) -> str:
    other = (
        f'<a class="print-link" href="/platform/learners/{enrollment_id}/card">البطاقة المفصلة A4</a>'
        if kind == "mini" else
        f'<a class="print-link" href="/platform/learners/{enrollment_id}/card/mini">البطاقة الصغيرة</a>'
    )
    return (f'<div class="toolbar no-print">'
            '<button type="button" onclick="window.print()">طباعة البطاقة أو حفظ PDF</button>'
            f'{other}<a class="back" href="/platform/learners/{enrollment_id}">العودة إلى ملف المستفيد</a>'
            '</div>')


def shared_css() -> str:
    return """
*{box-sizing:border-box}
body{margin:0;background:#edf2f6;color:#173650;font-family:Tahoma,Arial,sans-serif}
.toolbar{max-width:210mm;margin:17px auto 10px;display:flex;gap:9px;align-items:center;
  flex-wrap:wrap;justify-content:flex-start}
.toolbar button,.toolbar a{display:inline-flex;align-items:center;justify-content:center;min-height:38px;
  padding:9px 16px;border-radius:8px;border:1px solid #0e5580;background:#125780;color:white;
  text-decoration:none;font:600 13px Tahoma,Arial,sans-serif;cursor:pointer}
.toolbar .print-link{background:#e9f7f2;color:#086845;border-color:#92c5b1}
.toolbar .back{background:white;color:#26536c;border-color:#cbd8df}
.portrait{display:block;object-fit:cover;object-position:center;width:100%;height:100%;
  border-radius:12px;background:#f4f7fa;border:2px solid #dce7ee}
.portrait-empty{display:flex;align-items:center;justify-content:center;flex-direction:column;
  gap:5px;color:#71899a;font-size:10px;text-align:center}
.portrait-empty svg{height:53%;width:70%}
.brand-logo{object-fit:contain;background:white}
.info{display:flex;flex-direction:column;gap:5px;padding:11px 10px;border-bottom:1px solid #e5edf2;
  min-width:0;overflow-wrap:anywhere}
.field-label{font-size:11px;color:#557084}
.field-value{font-size:13px;font-weight:700;line-height:1.5;color:#183b55;white-space:pre-wrap}
@media print{
body{background:white}
.toolbar,.no-print{display:none!important}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def render_a4_card(enrollment: Enrollment, learner: Learner, center: Center, track: Track,
                   year: Year, portrait: str) -> HTMLResponse:
    personal = (
        info("الاسم الكامل", learner.full_name) +
        info("الرمز الأصلي", learner.source_code) +
        info("الجنس", learner.gender) +
        info("تاريخ الازدياد", learner.born_on.isoformat() if learner.born_on else "") +
        info("مكان الازدياد", learner.birthplace) +
        info("هاتف المستفيد", learner.phone) +
        info("العنوان", learner.address)
    )
    guardian = (info("اسم ولي الأمر", learner.guardian_name) +
                info("صلة القرابة", learner.guardian_relationship) +
                info("هاتف ولي الأمر", learner.guardian_phone))
    school = (
        info("المركز", center.name) + info("السنة التكوينية", year.label) +
        info("المسلك المهني", track.name) +
        info("رقم الملف داخل المنصة", enrollment.id) +
        info("تاريخ التسجيل", enrollment.enrolled_on.isoformat() if enrollment.enrolled_on else "") +
        info("الحالة", "مسجل" if enrollment.status == "active" else enrollment.status)
    )
    return HTMLResponse(f"""<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>بطاقة معلومات المستفيد | {e(learner.full_name)}</title>
<style>{shared_css()}
@page{{size:A4 portrait;margin:0}}
.sheet{{width:210mm;min-height:287mm;margin:10px auto 26px;background:#fff;
  box-shadow:0 12px 36px #17365017;overflow:hidden}}
.top-band{{height:7mm;background:linear-gradient(90deg,#0e7a64 0%,#0e7a64 28%,#12547c 28%,#12547c 100%)}}
.inner{{padding:12mm 16mm 14mm}}
.brand{{display:flex;align-items:center;justify-content:space-between;gap:14px;border-bottom:1px solid #dbe7ed;
  padding-bottom:11px}}
.brand-right{{display:flex;align-items:center;gap:13px}}
.brand-logo{{width:65px;height:65px;border-radius:9px}}
.org-name{{font-size:22px;font-weight:700;color:#10527f}}
.org-sub{{font-size:12px;color:#59738a;margin-top:4px}}
.serial{{font-size:11px;color:#427284;white-space:nowrap;border:1px solid #b8d4d0;
  background:#eff9f4;padding:8px 11px;border-radius:7px}}
.hero{{display:flex;flex-direction:row;align-items:center;gap:22px;padding:22px 0 18px;
  border-bottom:3px solid #d9ede6}}
.hero-photo{{width:36mm;height:45mm;flex:0 0 36mm}}
.hero-text{{min-width:0;flex:1}}
.eyebrow{{display:inline-block;color:#0c785b;background:#eaf6f1;border-radius:6px;
  padding:6px 12px;font-size:12px;font-weight:700}}
.hero h1{{font-size:25px;line-height:1.55;color:#124b78;margin:11px 0 3px;overflow-wrap:anywhere}}
.hero p{{margin:4px 0;color:#4e6b81;font-size:13px;line-height:1.6}}
.panel{{margin-top:19px;border:1px solid #dce7ed;border-radius:11px;overflow:hidden;
  break-inside:avoid}}
.panel h2{{font-size:15px;margin:0;background:#eff6fa;border-right:4px solid #14876a;
  color:#104f75;padding:11px 13px}}
.items{{display:grid;grid-template-columns:1fr 1fr;padding:3px 10px 10px;column-gap:14px}}
.footer{{display:flex;align-items:center;justify-content:space-between;gap:18px;
  border-top:1px solid #d8e4ea;margin-top:24px;padding-top:12px}}
.footer-note{{color:#687f90;font-size:10px;line-height:1.8;max-width:65%}}
.stamp{{display:flex;gap:15px;color:#335c70;font-size:11px}}
.stamp span{{border-top:1px solid #b5cbd6;padding-top:7px;min-width:60px;text-align:center}}
@media(max-width:820px){{.sheet{{width:100%;min-height:0;margin:10px 0}}.inner{{padding:18px}}
.hero-photo{{width:112px;height:145px;flex-basis:112px}}.items{{grid-template-columns:1fr 1fr}}}}
@media print{{.sheet{{width:210mm;min-height:0;margin:0;box-shadow:none}}
.inner{{padding:10mm 13mm 12mm}}.panel{{margin-top:12px}}
.hero{{padding:13px 0}}.hero-photo{{width:32mm;height:40mm;flex-basis:32mm}}}}
</style></head><body>
{toolbar(enrollment.id,"a4")}
<article class="sheet">
<div class="top-band"></div><div class="inner">
<header class="brand">
<div class="brand-right"><img class="brand-logo" alt="شعار جمعية نور الأمل"
src="/static/noor_official_logo.webp">
<div><div class="org-name">جمعية نور الأمل</div>
<div class="org-sub">برنامج الفرصة الثانية – الجيل الجديد</div></div></div>
<span class="serial">ملف رقم {enrollment.id:05d}</span>
</header>
<div class="hero">
<div class="hero-photo">{portrait}</div>
<div class="hero-text"><span class="eyebrow">بطاقة معلومات المستفيد</span>
<h1>{e(learner.full_name)}</h1><p>{e(center.name)}</p>
<p>{e(track.name)} · {e(year.label)}</p></div>
</div>
<section class="panel"><h2>المعلومات الشخصية</h2><div class="items">{personal}</div></section>
<section class="panel"><h2>معلومات ولي الأمر</h2><div class="items">{guardian}</div></section>
<section class="panel"><h2>معلومات التسجيل والتكوين</h2><div class="items">{school}</div></section>
<footer class="footer">
<p class="footer-note">وثيقة داخلية تحتوي بيانات شخصية. الخانات التي تظهر فيها «لم تُستكمل بعد»
تُستكمل من ملف المستفيد. تُحفظ لدى الجهات المخوّل لها فقط.</p>
<div class="stamp"><span>توقيع المسؤول</span><span>خاتم الجمعية</span></div>
</footer>
</div></article></body></html>""")


def render_mini_card(enrollment: Enrollment, learner: Learner, center: Center,
                     track: Track, year: Year, portrait: str) -> HTMLResponse:
    code = e(learner.source_code) if learner.source_code else "—"
    return HTMLResponse(f"""<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>بطاقة المستفيد الصغيرة | {e(learner.full_name)}</title>
<style>{shared_css()}
@page{{size:A4 portrait;margin:12mm}}
.mini-sheet{{max-width:210mm;margin:18px auto;padding:18px 10px;display:flex;justify-content:center}}
.mini-card{{width:85.6mm;height:54mm;flex:0 0 85.6mm;border-radius:3mm;overflow:hidden;
  background:white;border:1px solid #bdcfd9;box-shadow:0 7px 18px #15344c16;
  display:flex;flex-direction:column}}
.mini-header{{display:flex;align-items:center;gap:2.5mm;height:12.5mm;flex:0 0 12.5mm;
  background:#12537d;padding:1.5mm 3mm;color:white;border-bottom:1.2mm solid #0b936b}}
.mini-header .brand-logo{{width:10.5mm;height:9.5mm;border-radius:1.5mm}}
.mini-header strong{{font-size:10px;display:block}}
.mini-header small{{font-size:7px;display:block;margin-top:1mm}}
.mini-body{{display:flex;gap:3mm;padding:2.7mm;flex:1;min-height:0}}
.mini-portrait{{width:23mm;height:32mm;flex:0 0 23mm}}
.mini-info{{flex:1;min-width:0;overflow:hidden}}
.mini-tag{{font-size:8px;color:#07855d;font-weight:bold}}
.mini-info h1{{font-size:13px;margin:1mm 0;line-height:1.2;color:#124f78;
  overflow-wrap:anywhere;max-height:12mm;overflow:hidden}}
.mini-info p{{font-size:8px;line-height:1.35;margin:1mm 0;overflow-wrap:anywhere}}
.mini-info strong{{color:#19425a}}
.mini-bottom{{display:flex;align-items:center;justify-content:space-between;gap:2mm;
  padding:1mm 3mm;background:#e8f6ee;font-size:7px;color:#246c56;border-top:1px solid #c7e3d3}}
.small-note{{max-width:210mm;margin:0 auto;color:#5e7889;font-size:12px;text-align:center}}
@media print{{.mini-sheet{{width:100%;margin:0;max-width:none;padding:0;justify-content:flex-start}}
.mini-card{{box-shadow:none;border-color:#95adb8}}.small-note{{display:none}}}}
</style></head><body>
{toolbar(enrollment.id,"mini")}
<div class="mini-sheet"><article class="mini-card">
<header class="mini-header">
<img class="brand-logo" src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل">
<div><strong>جمعية نور الأمل</strong><small>برنامج الفرصة الثانية – الجيل الجديد</small></div>
</header>
<div class="mini-body">
<div class="mini-portrait">{portrait}</div>
<div class="mini-info"><span class="mini-tag">بطاقة المستفيد · {e(year.label)}</span>
<h1>{e(learner.full_name)}</h1>
<p><strong>الرمز:</strong> <bdi>{code}</bdi></p>
<p><strong>المركز:</strong> {e(center.name)}</p>
<p><strong>المسلك:</strong> {e(track.name)}</p>
</div></div>
<footer class="mini-bottom"><span>رقم الملف: {enrollment.id:05d}</span>
<span>بطاقة داخلية · ليست وثيقة هوية رسمية</span></footer>
</article></div>
<p class="small-note">حجم البطاقة 85.6 × 54 ملم. عند الطباعة اختَر A4 وحجم 100% لطباعة الأبعاد الحقيقية.</p>
</body></html>""")


@app.get("/platform/learners/{enrollment_id:int}/card/mini", response_class=HTMLResponse)
def learner_print_mini_card(
    enrollment_id: int, db: Session = Depends(get_db), user=Depends(actor)
):
    enrollment, learner = can_manage_learner(db, user, enrollment_id)
    return render_card(db, enrollment, learner, kind="mini")
