"""Own-timetable proposals by educators and instructors, reviewed before activation."""
from __future__ import annotations

from datetime import date

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import app, get_db
from platform_shift_rotation import SHIFT_OPTIONS, SHIFT_LABELS, slot_shift
from platform_v2 import (
    actor, area, assert_csrf, by_id, center_allowed, choose, coordinator, e,
    field, form, log, notify, page, redirect, require_role, slot_scope,
    validate_schedule_slot, WEEKDAY_NAMES, SUBJECTS,
)
from platform_v2_models import (
    Assignment, Center, Notification, ScheduleProposal, SessionDay, Slot, Staff, Track, now,
)


def slot_fingerprint(slot: Slot) -> str:
    return "/".join(str(v) for v in (
        slot.id, slot.year_id, slot.center_id, slot.track_id, slot.staff_id,
        slot.subject, slot.kind, slot.weekday, slot.starts_at, slot.ends_at,
        slot.specific_date.isoformat() if slot.specific_date else "",
        int(slot.canceled),
    ))


def own_tracks(db: Session, user) -> list[Track]:
    ids = {
        row.track_id for row in user["assignments"]
        if row.track_id and row.center_id
    }
    if not ids:
        return []
    tracks = db.scalars(select(Track).where(Track.id.in_(ids))).all()
    return [t for t in tracks if any(
        a.track_id == t.id and a.center_id == t.center_id
        for a in user["assignments"]
    )]


def authorized_subject(user, track: Track, subject: str) -> bool:
    if user["role"] not in ("instructor", "educator"):
        return False
    if user["role"] == "instructor" and subject != "مهني":
        return False
    if user["role"] == "educator" and subject not in SUBJECTS:
        return False
    return any(
        a.center_id == track.center_id and a.track_id == track.id
        and a.subject == ("" if subject == "مهني" else subject)
        for a in user["assignments"]
    )


def proposal_fields(db: Session, user, slot: Slot | None = None):
    tracks = own_tracks(db, user)
    centers = by_id(db, Center, (t.center_id for t in tracks))
    choices = [
        (track.id, f"{track.name} · {centers[track.center_id].name}")
        for track in tracks
    ]
    subjects = (
        [("مهني", "التكوين المهني")] if user["role"] == "instructor"
        else [(name, name) for name in SUBJECTS if any(
            a.track_id == t.id and a.center_id == t.center_id and a.subject == name
            for t in tracks for a in user["assignments"]
        )]
    )
    return (
        choose("track_id", "المجموعة والمركز", choices, selected=slot.track_id if slot else None)
        + choose("subject", "المادة", subjects, selected=slot.subject if slot else None)
        + choose("weekday", "اليوم", list(enumerate(WEEKDAY_NAMES)),
                 selected=slot.weekday if slot else None)
        + choose("shift", "نوع الأسبوع", SHIFT_OPTIONS,
                 selected=slot_shift(slot) if slot else "both")
        + field("starts_at", "من", "time", value=slot.starts_at if slot else "")
        + field("ends_at", "إلى", "time", value=slot.ends_at if slot else "")
        + field("specific_date", "تاريخ استثنائي (اختياري)", "date", False,
                slot.specific_date.isoformat() if slot and slot.specific_date else "")
        + '<label>سبب التغيير أو الإضافة<textarea name="reason" required '
          'minlength="5" maxlength="1000" rows="3"></textarea></label>'
        + (f'<input type="hidden" name="slot_id" value="{slot.id}">' if slot else "")
    )


@app.get("/platform/schedule/proposals", response_class=HTMLResponse)
def schedule_proposals_page(request: Request, db: Session = Depends(get_db),
                            user=Depends(actor)):
    csrf = request.session["csrf"]
    staff = user["role"] in ("instructor", "educator")
    if not staff and user["role"] not in ("admin", "coordinator"):
        raise HTTPException(403)
    proposals = db.scalars(
        select(ScheduleProposal).order_by(
            ScheduleProposal.id.desc()
        ).limit(300)
    ).all()
    if staff:
        proposals = [p for p in proposals if p.staff_id == user["id"]]
    else:
        proposals = [p for p in proposals if center_allowed(user, p.center_id)]
    centers = by_id(db, Center, (p.center_id for p in proposals))
    staff_by_id = by_id(db, Staff, (p.staff_id for p in proposals))
    tracks = by_id(db, Track, (p.track_id for p in proposals))
    status_text = {"pending": "بانتظار المصادقة", "approved": "مصادق عليه",
                   "rejected": "مرفوض"}
    rows = []
    for p in proposals:
        action = ""
        if not staff and p.status == "pending":
            action = (
                form(f"/platform/schedule/proposals/{p.id}/review", csrf,
                     '<input type="hidden" name="decision" value="approved">',
                     "مصادقة وتفعيل")
                + form(f"/platform/schedule/proposals/{p.id}/review", csrf,
                       '<input type="hidden" name="decision" value="rejected">'
                       + '<label>سبب الرفض<textarea name="comment" '
                         'required minlength="5"></textarea></label>',
                       "إرجاع / رفض")
            )
        rows.append([
            e(centers[p.center_id].name),
            e(staff_by_id[p.staff_id].full_name),
            "تعديل حصة" if p.slot_id else "إضافة حصة",
            e(tracks[p.track_id].name),
            e(p.subject),
            WEEKDAY_NAMES[p.weekday],
            e(f"{p.starts_at}–{p.ends_at}"),
            e(SHIFT_LABELS.get(p.shift, p.shift)),
            e(status_text.get(p.status, p.status)),
            e(p.reason),
            e(p.review_comment or "—"),
            action or "—",
        ])
    heading = (
        '<section class="pv2-proposal-panel">'
        '<h2>طلبات اعتماد استعمال الزمن</h2>'
        '<p class="subtle">اقتراحات الأطر لا تغيّر الحصص الفعلية حتى '
        'تتم مراجعتها والمصادقة عليها من المنسق أو إدارة الجمعية.</p>'
    )
    if staff:
        tracks_for_user = own_tracks(db, user)
        heading += (
            '<h3>اقتراح حصة جديدة</h3>'
            + form("/platform/schedule/proposals", csrf,
                   proposal_fields(db, user), "إرسال الحصة للمصادقة")
            if tracks_for_user else
            '<p class="subtle">لا توجد مجموعات مسندة لك بعد.</p>'
        )
    return page(
        request, user, "طلبات استعمال الزمن",
        heading + '</section><section><h2>الطلبات السابقة والحالية</h2>'
        + __import__("platform_v2").table([
            "المركز", "المؤطر", "العملية", "المجموعة", "المادة",
            "اليوم", "التوقيت", "الأسبوع", "الحالة", "المبرر",
            "ملاحظة المراجع", "القرار",
        ], rows) + '</section>',
    )


@app.get("/platform/schedule/{slot_id:int}/propose", response_class=HTMLResponse)
def propose_slot_edit_page(slot_id: int, request: Request,
                           db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "instructor", "educator")
    slot = slot_scope(db, user, slot_id, for_write=True)
    if slot.canceled:
        raise HTTPException(409, "لا يمكن تعديل حصة ملغاة")
    return page(
        request, user, "اقتراح تعديل استعمال الزمن",
        '<section class="pv2-proposal-panel"><h2>اقتراح تعديل حصتي</h2>'
        '<p class="subtle">التعديل لن يطبّق حتى يصادق عليه المنسق أو الإدارة. '
        'تظل الحصة الحالية فعّالة أثناء المراجعة.</p>'
        + form("/platform/schedule/proposals", request.session["csrf"],
               proposal_fields(db, user, slot),
               "إرسال تعديل الحصة للمصادقة")
        + '</section>',
    )


@app.post("/platform/schedule/proposals")
def submit_schedule_proposal(
    request: Request, token: str = Form(alias="_csrf"),
    track_id: int = Form(), subject: str = Form(),
    weekday: int = Form(), starts_at: str = Form(),
    ends_at: str = Form(), shift: str = Form("both"),
    specific_date: date | None = Form(None),
    slot_id: int = Form(0), reason: str = Form(),
    db: Session = Depends(get_db), user=Depends(actor),
):
    require_role(user, "instructor", "educator")
    assert_csrf(request, token)
    track = db.get(Track, track_id)
    if not track or not authorized_subject(user, track, subject):
        raise HTTPException(403, "المادة أو المجموعة غير مسندة إليك")
    if not (0 <= weekday <= 6
            and len(starts_at) == 5 and len(ends_at) == 5
            and "00:00" <= starts_at < ends_at <= "23:59"
            and starts_at[2] == ends_at[2] == ":"
            and starts_at[:2].isdigit() and starts_at[3:].isdigit()
            and ends_at[:2].isdigit() and ends_at[3:].isdigit()
            and 0 <= int(starts_at[3:]) <= 59
            and 0 <= int(ends_at[3:]) <= 59
            and (specific_date is None or specific_date.weekday() == weekday)):
        raise HTTPException(422, "توقيت الحصة أو اليوم غير صحيح")
    if shift not in SHIFT_LABELS or not 5 <= len(reason.strip()) <= 1000:
        raise HTTPException(422, "نوع الأسبوع أو المبرر غير صحيح")
    original = None
    if slot_id:
        original = slot_scope(db, user, slot_id, for_write=True)
        if original.canceled or original.center_id != track.center_id:
            raise HTTPException(409, "لا يمكن نقل الحصة إلى مركز آخر")
    proposal = ScheduleProposal(
        center_id=track.center_id, staff_id=user["id"],
        track_id=track.id, slot_id=original.id if original else None,
        baseline=slot_fingerprint(original) if original else "",
        subject=subject, weekday=weekday,
        starts_at=starts_at, ends_at=ends_at,
        shift=shift, specific_date=specific_date,
        reason=reason.strip(), status="pending",
    )
    db.add(proposal)
    db.flush()
    notify(db, track.center_id, "اقتراح استعمال زمن جديد ينتظر المصادقة",
           "/platform/schedule/proposals")
    log(db, user, "propose_schedule", "schedule_proposal",
        proposal.id, proposal.reason)
    db.commit()
    return redirect("/platform/schedule/proposals")


@app.post("/platform/schedule/proposals/{proposal_id}/review")
def review_schedule_proposal(
    proposal_id: int, request: Request,
    token: str = Form(alias="_csrf"), decision: str = Form(),
    comment: str = Form(""),
    db: Session = Depends(get_db), user=Depends(actor),
):
    assert_csrf(request, token)
    p = db.get(ScheduleProposal, proposal_id)
    if not p:
        raise HTTPException(404, "الطلب غير موجود")
    coordinator(user, p.center_id)
    if p.status != "pending" or decision not in ("approved", "rejected"):
        raise HTTPException(409, "سبق البت في هذا الطلب")
    if decision == "rejected" and len(comment.strip()) < 5:
        raise HTTPException(422, "يجب تعليل رفض الطلب")
    if decision == "approved":
        original = db.get(Slot, p.slot_id) if p.slot_id else None
        if p.slot_id and (
            original is None or original.canceled or
            slot_fingerprint(original) != p.baseline or
            original.staff_id != p.staff_id
        ):
            raise HTTPException(409, "تغيرت الحصة منذ تقديم الطلب؛ يجب تقديم طلب جديد")
        # An instructor may have lost the track assignment during review.
        teacher = db.get(Staff, p.staff_id)
        if not teacher or not teacher.enabled or not teacher.role in ("instructor", "educator"):
            raise HTTPException(409, "حساب المؤطر لم يعد مؤهلاً")
        has_history = bool(original and db.scalar(
            select(SessionDay.id).where(SessionDay.slot_id == original.id).limit(1)
        ))
        if has_history:
            original.canceled = True
            db.flush()
        try:
            revised = validate_schedule_slot(
                db, p.track_id, p.staff_id, p.subject, p.weekday,
                p.starts_at, p.ends_at, p.specific_date, p.reason,
                user, existing=original if original and not has_history else None,
                shift=p.shift,
            )
            if revised.center_id != p.center_id:
                raise HTTPException(409, "المركز لم يعد مطابقاً للطلب")
            db.add(revised)
            db.flush()
        except (HTTPException, IntegrityError):
            db.rollback()
            raise
        if has_history:
            log(db, user, "schedule_supersede", "slot", original.id,
                f"طلب #{p.id}؛ الإصدار الجديد #{revised.id}")
        log(db, user, "approve_schedule", "slot", revised.id, f"طلب #{p.id}")
    else:
        log(db, user, "reject_schedule", "schedule_proposal", p.id, comment)
    p.status = decision
    p.reviewed_by = user["name"]
    p.reviewed_at = now()
    p.review_comment = comment.strip()[:1500]
    db.add(Notification(
        staff_id=p.staff_id,
        message=("تمت المصادقة على تعديل استعمال الزمن"
                 if decision == "approved" else
                 "تم إرجاع طلب استعمال الزمن: " + comment.strip()[:170]),
        target="/platform/schedule/proposals",
    ))
    db.commit()
    return redirect("/platform/schedule/proposals")
