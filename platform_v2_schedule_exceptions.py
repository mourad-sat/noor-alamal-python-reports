"""Exceptional session cancellation and make-up scheduling with audit trail.

Cancellation is date-scoped: the fixed weekly timetable remains intact. A
coordinator can cancel one occurrence without recording learners as absent.
"""
from __future__ import annotations

from datetime import date

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, schedule_manager, log
from platform_v2_models import (
    Attendance, Closure, Notification, SessionDay, SessionReport, Slot,
)

@app.post("/platform/schedule/{slot_id}/cancel-day")
def cancel_slot_day(slot_id:int,request:Request,
                    token:str=Form(alias="_csrf"),held_on:date=Form(),
                    reason:str=Form(),
                    db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    slot=db.get(Slot,slot_id)
    if not slot:raise HTTPException(404,"الحصة غير موجودة")
    schedule_manager(user,slot.center_id)
    if slot.canceled or held_on.weekday()!=slot.weekday or (
        slot.specific_date and slot.specific_date!=held_on
    ):
        raise HTTPException(422,"التاريخ لا يطابق الحصة")
    if len(reason.strip())<8:
        raise HTTPException(422,"سبب الإلغاء أو التأجيل إلزامي")
    if db.scalar(select(Closure).where(
        Closure.center_id==slot.center_id,Closure.closed_on==held_on)):
        raise HTTPException(409,"اليوم مغلق بالفعل")
    session=db.scalar(select(SessionDay).where(
        SessionDay.slot_id==slot_id,SessionDay.held_on==held_on))
    if session:
        if session.status=="canceled":
            raise HTTPException(409,"هذه الحصة ملغاة مسبقًا")
        if db.scalar(select(Attendance).where(Attendance.session_id==session.id)):
            raise HTTPException(409,"الحصة لها حضور مسجل؛ يلزم تسوية السجل قبل الإلغاء")
        if db.scalar(select(SessionReport).where(SessionReport.session_id==session.id)):
            raise HTTPException(409,"الحصة لها تقرير؛ يلزم مراجعته قبل الإلغاء")
        session.status="canceled"
    else:
        session=SessionDay(slot_id=slot.id,held_on=held_on,status="canceled")
        db.add(session)
    try:
        db.flush()
        log(db,user,"cancel_session_occurrence","session",session.id,reason)
        db.add(Notification(staff_id=slot.staff_id,
            message=f"أُلغيت حصة {slot.subject} بتاريخ {held_on}: {reason.strip()[:150]}",
            target="/platform/schedule"))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,"التعديل سبق تسجيله")
    return RedirectResponse("/platform/schedule",status_code=303)
