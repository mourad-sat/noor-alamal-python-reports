"""Private activity photo album using existing approved-report attachments only.

No new storage or schema. Images are displayed only after a coordinator has
explicitly selected them for internal reporting. The album is *not* public
consent or authorization to publish learner portraits externally.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (
    actor, assert_csrf, center_allowed, coordinator, e, page, post_fields,
    redirect, require_role, visible_tracks, log,
)
from platform_v2_attachments import attachment_access
from platform_v2_models import (
    Attachment, Center, SessionDay, SessionReport, Slot, Track, Year,
)

PHOTO_TYPES = ("image/png", "image/jpeg")
PER_PAGE = 48

ALBUM_STYLE = """
<style>
.album-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
 gap:10px;align-items:end}
.album-filters label{font-size:13px;display:flex;flex-direction:column;gap:5px}
.album-filters input,.album-filters select{width:100%;min-width:0}
.album-toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}
.album-toolbar .button,.album-toolbar button{border-radius:7px}
.album-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
.album-card{border:1px solid #c9dedf;border-radius:9px;overflow:hidden;
 background:#fff;break-inside:avoid}
.album-card img{display:block;object-fit:contain;width:100%;height:190px;
 background:#e9f1f3}
.album-card .album-details{padding:10px 12px;font-size:12px;line-height:1.65}
.album-card strong{display:block;overflow-wrap:anywhere}
.album-card small{display:block;color:#586d7b;overflow-wrap:anywhere}
.album-card form{margin-top:8px}
.album-card button{font-size:12px}
.album-pending{padding:10px 12px;background:#f3f7f8;border:1px solid #d1e2e6;
 border-radius:8px;break-inside:avoid}
.album-pending small{display:block;color:#586d7b}
.album-pager{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:12px}
.album-tip{font-size:12px;color:#596f7d}
@media print{
 @page{size:A4 portrait;margin:13mm}
 .album-no-print,nav,header:not(.album-print-head){display:none!important}
 .album-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .album-card{box-shadow:none}
 .album-card img{height:160px}
 body{background:white!important}
}
</style>
"""


def photo_query(center_id=None, track_id=None, start=None, end=None, mode="selected"):
    q = (
        select(Attachment, SessionReport, SessionDay, Slot, Track)
        .join(SessionReport, Attachment.report_id == SessionReport.id)
        .join(SessionDay, SessionReport.session_id == SessionDay.id)
        .join(Slot, SessionDay.slot_id == Slot.id)
        .join(Track, Slot.track_id == Track.id)
        .where(
            Attachment.content_type.in_(PHOTO_TYPES),
            SessionReport.status == "approved",
            SessionDay.status != "canceled",
            Slot.center_id == Track.center_id,
            Slot.year_id == Track.year_id,
        )
    )
    if center_id is not None:
        q = q.where(Slot.center_id == center_id)
    if track_id is not None:
        q = q.where(Slot.track_id == track_id)
    if start is not None:
        q = q.where(SessionDay.held_on >= start)
    if end is not None:
        q = q.where(SessionDay.held_on <= end)
    if mode == "selected":
        q = q.where(Attachment.select_for_summary.is_(True))
    elif mode == "pending":
        q = q.where(Attachment.select_for_summary.is_(False))
    return q


def album_filters(db, user, center_id, track_id, start, end, mode):
    centers = db.scalars(select(Center).order_by(Center.name)).all()
    allowed_centers = [c for c in centers if center_allowed(user, c.id)]
    tracks = visible_tracks(db, user)
    opts_centers = '<option value="">كل المراكز المسموح بها</option>' + "".join(
        f'<option value="{c.id}" {"selected" if c.id == center_id else ""}>'
        f'{e(c.name)}</option>' for c in allowed_centers
    )
    opts_tracks = '<option value="">كل المسالك المسموح بها</option>' + "".join(
        f'<option value="{t.id}" {"selected" if t.id == track_id else ""}>'
        f'{e(db.get(Center,t.center_id).name)} · {e(t.name)} · '
        f'{e(db.get(Year,t.year_id).label)}</option>'
        for t in tracks if center_id is None or t.center_id == center_id
    )
    opts_modes = "".join(
        f'<option value="{key}" {"selected" if mode == key else ""}>{label}</option>'
        for key, label in (
            ("selected", "الصور المختارة للتقارير"),
            ("pending", "صور في انتظار الاختيار"),
        )
    )
    return (
        '<form class="album-filters album-no-print" method="get" '
        'action="/platform/activity-album">'
        f'<label>المركز<select name="center_id">{opts_centers}</select></label>'
        f'<label>المسلك<select name="track_id">{opts_tracks}</select></label>'
        f'<label>من تاريخ<input type="date" name="start" value="{e(start or "")}"></label>'
        f'<label>إلى تاريخ<input type="date" name="end" value="{e(end or "")}"></label>'
        f'<label>عرض<select name="mode">{opts_modes}</select></label>'
        '<button type="submit">تطبيق البحث</button></form>'
    )


@app.get("/platform/activity-album", response_class=HTMLResponse)
def activity_album(request: Request, center_id: int | None = None,
                   track_id: int | None = None,
                   start: date | None = None, end: date | None = None,
                   mode: str = "selected", page_number: int = 1,
                   db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    if mode not in ("selected", "pending") or page_number < 1:
        raise HTTPException(422, "اختيار العرض غير صالح")
    if start and end and start > end:
        raise HTTPException(422, "تاريخ البداية بعد تاريخ النهاية")
    tracks = visible_tracks(db, user)
    allowed_ids = {t.id for t in tracks}
    allowed_centers = {t.center_id for t in tracks}
    if center_id is not None and center_id not in allowed_centers:
        raise HTTPException(403, "المركز غير متاح")
    if track_id is not None and track_id not in allowed_ids:
        raise HTTPException(403, "المسلك غير متاح")
    if track_id is not None and center_id is not None:
        track = next(t for t in tracks if t.id == track_id)
        if track.center_id != center_id:
            raise HTTPException(422, "المسلك لا ينتمي إلى المركز")
    # Mandatory scope even without a selected center or track.
    if not allowed_ids:
        q = None
    else:
        q = photo_query(center_id, track_id, start, end, mode).where(
            Slot.track_id.in_(allowed_ids)
        )
    total = db.scalar(select(func.count()).select_from(q.subquery())) if q is not None else 0
    total = total or 0
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if page_number > pages:
        raise HTTPException(422, "رقم الصفحة خارج النطاق")
    photos = db.execute(
        q.order_by(SessionDay.held_on.desc(), Attachment.id.desc())
         .limit(PER_PAGE).offset((page_number - 1) * PER_PAGE)
    ).all() if q is not None else []

    csrf = request.session["csrf"]
    tiles = []
    for photo, report, day, slot, track in photos:
        center = db.get(Center, slot.center_id)
        subtitle = (
            f'{e(center.name)} · {e(track.name)} · '
            f'{e(day.held_on.isoformat())} · {e(slot.subject)}'
        )
        action = (
            f'<form method="post" action="/platform/activity-album/{photo.id}/select">'
            + post_fields(csrf)
            + '<button type="submit">'
            + ("إلغاء اختيار الصورة" if photo.select_for_summary else "اختيار للتقارير")
            + '</button></form>'
        )
        link = (
            f'<a href="/platform/reports/{report.id}/attachments">'
            'ملف المرفقات الأصلي</a>'
        )
        if photo.select_for_summary:
            tiles.append(
                '<figure class="album-card">'
                f'<img src="/platform/attachments/{photo.id}/view" '
                f'alt="{e(photo.original_name)}" loading="lazy">'
                '<figcaption class="album-details">'
                f'<strong>{e(photo.original_name)}</strong><small>{subtitle}</small>'
                f'<small>تقرير الحصة #{report.id}</small>{link}'
                f'<div class="album-no-print">{action}</div></figcaption></figure>'
            )
        else:
            # Never put unselected private photos into an <img> element.
            tiles.append(
                '<div class="album-pending">'
                f'<strong>{e(photo.original_name)}</strong>'
                f'<small>{subtitle} · تقرير الحصة #{report.id}</small>'
                '<p class="album-tip">الصورة خاصة ولا تُعرض قبل اختيارها '
                'من المسؤول المخوّل بعد التأكد من ملاءمة استعمالها.</p>'
                f'{link}{action}</div>'
            )

    filters = {
        "mode": mode,
        **({"center_id": center_id} if center_id is not None else {}),
        **({"track_id": track_id} if track_id is not None else {}),
        **({"start": start.isoformat()} if start else {}),
        **({"end": end.isoformat()} if end else {}),
    }
    next_link = (
        f'<a class="button" href="/platform/activity-album?'
        f'{urlencode({**filters,"page_number":page_number+1})}">التالي</a>'
        if page_number < pages else ""
    )
    prev_link = (
        f'<a class="button" href="/platform/activity-album?'
        f'{urlencode({**filters,"page_number":page_number-1})}">السابق</a>'
        if page_number > 1 else ""
    )
    title = "ألبوم الأنشطة التوثيقية"
    info = (
        '<section><h2>ألبوم الأنشطة التوثيقية</h2>'
        '<p class="album-tip">صور مرفقات تقارير الحصص المعتمدة فقط، '
        'مرتبة حسب تاريخ الحصة والمركز والمسلك. '
        'اختيار صورة هنا يُدرجها في التقارير الداخلية، ولا يُعد موافقة '
        'على النشر الخارجي أو استعمال صور المستفيدين دون إذن.</p>'
        + album_filters(db, user, center_id, track_id, start, end, mode)
        + '<div class="album-toolbar album-no-print">'
        f'<strong>{total} صورة في هذا العرض · صفحة {page_number} من {pages}</strong>'
        '<button type="button" onclick="window.print()">طباعة الألبوم / حفظ PDF</button>'
        '<a class="button" href="/platform/reports">تقارير الحصص ورفع الصور</a>'
        '</div></section>'
    )
    gallery = (
        '<section><h2>'
        + ("الصور المختارة" if mode == "selected" else "صور في انتظار الاختيار")
        + '</h2>'
        + (f'<div class="album-grid">{"".join(tiles)}</div>' if tiles else
           '<p>لا توجد صور ضمن المركز والمسلك والفترة المختارة.</p>')
        + f'<nav class="album-pager album-no-print">{prev_link}{next_link}</nav>'
        + '</section>'
    )
    return page(request, user, title, ALBUM_STYLE + info + gallery)


@app.post("/platform/activity-album/{attachment_id:int}/select")
def album_select_photo(request: Request, attachment_id: int,
                       token: str = Form(alias="_csrf"),
                       db: Session = Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    require_role(user, "admin", "coordinator")
    photo, report, slot = attachment_access(db, user, attachment_id)
    coordinator(user, slot.center_id)
    day = db.get(SessionDay, report.session_id)
    track = db.get(Track, slot.track_id)
    if (
        report.status != "approved" or day.status == "canceled"
        or slot.center_id != track.center_id or slot.year_id != track.year_id
        or photo.content_type not in PHOTO_TYPES
    ):
        raise HTTPException(409, "لا يمكن اختيار صورة خارج تقرير حصة معتمد وسليم")
    photo.select_for_summary = not photo.select_for_summary
    log(db, user, "attachment_select", "attachment", photo.id, str(photo.select_for_summary))
    db.commit()
    return redirect("/platform/activity-album?mode="
                    + ("selected" if photo.select_for_summary else "pending"))
