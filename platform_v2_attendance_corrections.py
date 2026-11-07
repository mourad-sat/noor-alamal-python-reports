"""Reasoned coordinator approval for corrections to already-approved attendance.

An approved session report locks attendance. Requests from the responsible
teacher do not alter official data until the center coordinator approves.
"""
from __future__ import annotations

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, coordinator, e, form, area, choose, log, notify, page
from platform_v2_models import (
    Attendance, AttendanceCorrection, Enrollment, SessionDay, SessionReport, Slot, now,
)

@app.get("/platform/attendance/corrections", response_class=HTMLResponse)
def corrections_page(request: Request, db: Session=Depends(get_db), user=Depends(actor)):
    if user["role"] not in ("admin", "coordinator"):
        raise HTTPException(403, "مراجعة تصحيحات الحضور من صلاحيات المنسق")
    entries = db.scalars(
        select(AttendanceCorrection).order_by(AttendanceCorrection.created_at.desc()).limit(300)
    ).all()
    rows = []
    csrf = request.session["csrf"]
    for item in entries:
        attendance = db.get(Attendance, item.attendance_id)
        if not attendance:
            continue
        session = db.get(SessionDay, attendance.session_id)
        slot = db.get(Slot, session.slot_id)
        if user["role"] == "coordinator" and not any(
            assignment.center_id == slot.center_id for assignment in user["assignments"]
        ):
            continue
        review = "—"
        if item.status == "pending" and user["role"] == "coordinator":
            review = form(f"/platform/attendance/corrections/{item.id}/review", csrf,
                '<input name="decision" type="hidden" value="approved">'
                + area("comment", "ملاحظة المنسق (اختيارية)"), "اعتماد التصحيح")
            review += form(f"/platform/attendance/corrections/{item.id}/review", csrf,
                '<input name="decision" type="hidden" value="rejected">'
                + area("comment", "سبب الرفض"), "رفض الطلب")
        rows.append([
            e(item.id), e(session.held_on), e(attendance.enrollment_id),
            e(attendance.status), e(item.new_status), e(item.reason),
            e(item.status), review,
        ])
    return page(request, user, "مراجعة تصحيحات الحضور",
        '<section><h2>طلبات تصحيح السجلات المعتمدة</h2>'
        + '<p>لا يتغير سجل الحضور الرسمي حتى يوافق منسق المركز.</p>'
        + __import__("platform_v2").table(
            ["الطلب", "تاريخ الحصة", "رقم التسجيل", "القائم", "المطلوب", "التعليل", "الحالة", "المراجعة"],
            rows
        ) + '</section>')

@app.post("/platform/attendance/{attendance_id}/correction")
def request_attendance_correction(attendance_id: int, request: Request,
                                  token: str=Form(alias="_csrf"),
                                  new_status: str=Form(),
                                  reason: str=Form(),
                                  db: Session=Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    if user["role"] not in ("instructor", "educator"):
        raise HTTPException(403, "الطلب من صلاحيات مؤطر الحصة")
    a = db.get(Attendance, attendance_id)
    if not a:
        raise HTTPException(404)
    session = db.get(SessionDay, a.session_id)
    slot = db.get(Slot, session.slot_id)
    if user["id"] != slot.staff_id:
        raise HTTPException(403, "هذه الحصة غير مسندة إليك")
    report = db.scalar(select(SessionReport).where(SessionReport.session_id == session.id))
    if not report or report.status != "approved":
        raise HTTPException(409, "هذه الحصة غير معتمدة بعد")
    if new_status not in ("present", "absent", "late") or new_status == a.status:
        raise HTTPException(422, "الحالة المطلوبة غير صحيحة")
    if len(reason.strip()) < 8:
        raise HTTPException(422, "السبب المفصل للتصحيح إلزامي")
    active = db.scalar(select(AttendanceCorrection).where(
        AttendanceCorrection.attendance_id == a.id,
        AttendanceCorrection.status == "pending"))
    if active:
        raise HTTPException(409, "يوجد طلب تصحيح معلّق لهذا السجل")
    change = AttendanceCorrection(
        attendance_id=a.id, requested_by=user["id"],
        old_status=a.status, new_status=new_status, reason=reason.strip()[:2000])
    db.add(change)
    db.flush()
    enrollment = db.get(Enrollment, a.enrollment_id)
    notify(db, enrollment.center_id, "تصحيح حضور معتمد ينتظر المصادقة", "/platform/attendance/corrections")
    log(db, user, "request_attendance_correction", "attendance", a.id)
    db.commit()
    return RedirectResponse(f"/platform/sessions/{session.id}", status_code=303)

@app.post("/platform/attendance/corrections/{correction_id}/review")
def review_attendance_correction(correction_id: int, request: Request,
                                 token: str=Form(alias="_csrf"),
                                 decision: str=Form(), comment: str=Form(""),
                                 db: Session=Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    item = db.get(AttendanceCorrection, correction_id)
    if not item or item.status != "pending":
        raise HTTPException(409, "الطلب غير متاح للمراجعة")
    attendance = db.get(Attendance, item.attendance_id)
    if not attendance:
        raise HTTPException(404)
    session = db.get(SessionDay, attendance.session_id)
    slot = db.get(Slot, session.slot_id)
    coordinator(user, slot.center_id)
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "القرار غير معروف")
    if decision == "rejected" and len(comment.strip()) < 5:
        raise HTTPException(422, "سبب الرفض إلزامي")
    if attendance.status != item.old_status:
        raise HTTPException(409, "تغير سجل الحضور بعد تقديم الطلب؛ يلزم طلب جديد")
    if decision == "approved":
        if session.status == "canceled":
            raise HTTPException(409, "لا يمكن تصحيح حصة ملغاة")
        attendance.status = item.new_status
        attendance.permit_id = None
        # Newly discovered absences remain red until a NEW entry permit is issued.
        log(db, user, "approve_attendance_correction", "attendance", attendance.id,
            f"{item.old_status}→{item.new_status}; reason={item.reason[:500]}")
    else:
        log(db, user, "reject_attendance_correction", "attendance", attendance.id, comment)
    item.status = decision
    item.reviewed_by = user["name"]
    item.reviewed_at = now()
    item.review_comment = comment.strip()[:1500]
    db.commit()
    return RedirectResponse("/platform/attendance/corrections", status_code=303)
