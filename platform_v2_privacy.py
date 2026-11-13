"""Administrator-only erasure with shared-evidence safety interlocks.

An erased learner's identity must not survive in grades, approvals, notice texts,
QR proofs, or application audit records. Previously encrypted backups and any
offline paper copies still require a separate legally compliant erasure policy.

The platform deliberately blocks deletion if any session evidence might contain
the learner. Human review and removal of those shared files is required first.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import APP_SECRET, Absence, Report, app, get_db
from platform_v2 import actor, assert_csrf, e, form, field, choose, area, page, require_role
from platform_v2_models import (
    Assessment, Attachment, AttachmentBlob, Attendance, AttendanceCorrection, Audit, CenterReport,
    Discipline, DocumentProof, Enrollment, ErasureAggregate, Grade,
    GradeCorrection, Incident, Learner, LearnerPhoto, Notice, Notification, Permit,
    SessionDay, SessionReport, SessionReportProof, Slot, SkillEvaluation,
)

ERASURE_CATEGORIES = {
    "data_subject": "طلب قانوني من صاحب الحق أو ولي الأمر",
    "duplicate": "تصحيح تسجيل مكرر",
    "legal": "أمر قانوني موثق",
    "other": "سبب إداري موثق آخر",
}

def affected_enrollments(db: Session, learner_id: int):
    return db.scalars(select(Enrollment).where(Enrollment.learner_id == learner_id)).all()

def shared_evidence_blockers(db: Session, enrollments: list[Enrollment]):
    """Identify all uploads for the person's tracks/years, not just attendance.

    An image can identify a learner even when their attendance was not marked.
    Such evidence is not uniquely owned by that learner, so it cannot be
    silently destroyed together with a shared classroom report.
    """
    track_years={(en.track_id,en.year_id) for en in enrollments}
    if not track_years:
        return []
    slots=db.scalars(select(Slot)).all()
    slot_ids={s.id for s in slots if (s.track_id,s.year_id) in track_years}
    if not slot_ids:return []
    sessions=db.scalars(select(SessionDay).where(SessionDay.slot_id.in_(slot_ids))).all()
    session_ids={s.id for s in sessions}
    if not session_ids:return []
    reports=db.scalars(select(SessionReport).where(SessionReport.session_id.in_(session_ids))).all()
    report_ids={r.id for r in reports}
    if not report_ids:return []
    return db.scalars(select(Attachment).where(Attachment.report_id.in_(report_ids)).order_by(Attachment.id)).all()

def scrub_prose(db:Session,learner:Learner,enrollments:list[Enrollment]):
    """Remove literal identifiers from free-text records that may be shared."""
    tokens={learner.full_name.strip(),learner.guardian_name.strip(),
            learner.guardian_phone.strip(),learner.phone.strip()}
    tokens={token for token in tokens if len(token)>=4}
    if not tokens:return
    field_sets=(
        (SessionReport,("subject","objectives","activities","results","challenges","recommendations","review_comment")),
        (CenterReport,("analysis","review_comment")),
        (Report,("subject","group_name","trainer_name","venue","objectives","activities","results","challenges","recommendations")),
        (Absence,("learner_name","group_name","notes")),
        (Audit,("reason",)),
        (Notification,("message",)),
    )
    for model,fields in field_sets:
        for row in db.scalars(select(model)).all():
            for attr in fields:
                value=getattr(row,attr,None)
                if not isinstance(value,str):continue
                redacted=value
                for token in tokens:
                    redacted=redacted.replace(token,"[بيانات محذوفة]")
                if redacted!=value:
                    setattr(row,attr,redacted)

def erase_learner(db:Session,user,learner_id:int,reason:str,category:str):
    require_role(user,"admin")
    learner=db.get(Learner,learner_id)
    if not learner:raise HTTPException(404,"لا يوجد ملف بهذا الرقم")
    if category not in ERASURE_CATEGORIES or len(reason.strip())<10:
        raise HTTPException(422,"اختر نوع الطلب وقدّم سببًا موثقًا لا يقل عن عشرة أحرف")

    enrollments=affected_enrollments(db,learner_id)
    blockers=shared_evidence_blockers(db,enrollments)
    if blockers:
        raise HTTPException(409,
           "تعذّر الحذف: توجد مرفقات لحصص المسلك قد تحتوي صورًا أو معلومات عن المستفيد. "
           "يجب مراجعتها ومعالجتها أولًا دون المساس غير الضروري بملفات الآخرين.")
    en_ids=[x.id for x in enrollments]
    years={x.year_id for x in enrollments}
    attendance=db.scalars(select(Attendance).where(Attendance.enrollment_id.in_(en_ids))).all() if en_ids else []
    attendance_ids=[x.id for x in attendance]
    grades=db.scalars(select(Grade).where(Grade.enrollment_id.in_(en_ids))).all() if en_ids else []
    grade_ids=[x.id for x in grades]
    incidents=db.scalars(select(Incident).where(Incident.enrollment_id.in_(en_ids))).all() if en_ids else []
    notices=db.scalars(select(Notice).where(Notice.enrollment_id.in_(en_ids))).all() if en_ids else []
    discipline=db.scalars(select(Discipline).where(Discipline.enrollment_id.in_(en_ids))).all() if en_ids else []
    permits=db.scalars(select(Permit).where(Permit.learner_id==learner.id)).all()
    proofs=db.scalars(select(DocumentProof).where(DocumentProof.enrollment_id.in_(en_ids))).all() if en_ids else []
    changes=db.scalars(select(GradeCorrection).where(GradeCorrection.grade_id.in_(grade_ids))).all() if grade_ids else []
    corrections=db.scalars(select(AttendanceCorrection).where(AttendanceCorrection.attendance_id.in_(attendance_ids))).all() if attendance_ids else []
    skill_evaluations=db.scalars(select(SkillEvaluation).where(SkillEvaluation.enrollment_id.in_(en_ids))).all() if en_ids else []

    sessions={s.id:s.held_on for s in db.scalars(select(SessionDay)).all()}
    missed=[a for a in attendance if a.status=="absent"]
    for year_id in years:
        en_set={en.id for en in enrollments if en.year_id==year_id}
        absences=[a for a in missed if a.enrollment_id in en_set]
        absent_days=len({(a.enrollment_id,sessions.get(a.session_id)) for a in absences})
        agg=db.get(ErasureAggregate,year_id)
        if not agg:
            agg=ErasureAggregate(year_id=year_id,erased_learners=0,absent_sessions=0,absent_days=0)
            db.add(agg)
        agg.erased_learners+=1
        agg.absent_sessions+=len(absences)
        agg.absent_days+=absent_days

    # Audit references to sensitive records must not survive as joinable IDs.
    id_references={
        "grade":grade_ids,"attendance":attendance_ids,"enrollment":en_ids,
        "incident":[x.id for x in incidents],"notice":[x.id for x in notices],
        "discipline":[x.id for x in discipline],"permit":[x.id for x in permits],
        "learner":[learner.id],"learner_photo":[learner.id],
        "skill_evaluation":[x.id for x in skill_evaluations],
    }
    for resource,ids in id_references.items():
        if ids:
            db.execute(delete(Audit).where(Audit.resource==resource,Audit.resource_id.in_(ids)))

    # The legacy absence table has no learner FK; remove exact-name records.
    db.execute(delete(Absence).where(Absence.learner_name==learner.full_name))
    # Report QR fingerprints of affected sessions may contain prior attendance
    # details. Revoke such proofs because their underlying record changed.
    affected_session_ids={item.session_id for item in attendance}
    if affected_session_ids:
        report_ids=db.scalars(select(SessionReport.id).where(
            SessionReport.session_id.in_(affected_session_ids))).all()
        if report_ids:
            db.execute(delete(SessionReportProof).where(
                SessionReportProof.report_id.in_(report_ids)))
    # System notifications sometimes display the learner's internal identifier.
    db.execute(delete(Notification).where(
        Notification.message.like(f"%المستفيد رقم {learner.id}%")))
    scrub_prose(db,learner,enrollments)
    for model,ids in [
        (GradeCorrection,[x.id for x in changes]),
        (AttendanceCorrection,[x.id for x in corrections]),
        (DocumentProof,[x.id for x in proofs]),
        (SkillEvaluation,[x.id for x in skill_evaluations]),
        (Grade,grade_ids),
        (Attendance,attendance_ids),
        (Incident,[x.id for x in incidents]),
        (Discipline,[x.id for x in discipline]),
        (Notice,[x.id for x in notices]),
        (Permit,[x.id for x in permits]),
    ]:
        if ids:
            db.execute(delete(model).where(model.id.in_(ids)))
    if en_ids:
        db.execute(delete(Enrollment).where(Enrollment.id.in_(en_ids)))
    # A private portrait is owned by this learner; remove it with the record.
    photo=db.get(LearnerPhoto,learner.id)
    if photo:
        db.delete(photo)
    db.delete(learner)

    # No PII, IDs or free-form reason are retained in the deletion receipt.
    # Preserve proof that a rationale was entered, without keeping identifiers.
    why=hmac.new(APP_SECRET.encode(),reason.strip().encode(),hashlib.sha256).hexdigest()[:16]
    db.add(Audit(actor=user["name"],action="irreversible_erasure",
                 resource="privacy",resource_id=0,
                 reason=f"category={category};reason_digest={why}"))
    db.commit()

@app.get("/platform/privacy/erase",response_class=HTMLResponse)
def erase_page(request:Request,learner_id:int|None=None,
               db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin")
    body='<section><h2>حذف نهائي مقيد بإدارة الجمعية</h2>'
    body+='<p>الحذف يشمل ملفات المستفيد ونقطه وغيابه وقراراته وإشعاراته ورموز QR. '
    body+='قبل التنفيذ يجب التأكد من معالجة الصور والوثائق المشتركة والنسخ الورقية والاحتياطية. '
    body+='النسخ الاحتياطية القديمة لا تُمحى من هذا النموذج وتحتاج إلى سياسة احتفاظ واستعادة تمنع إرجاع المعلومات المحذوفة.</p>'
    body+=form("/platform/privacy/erase",request.session["csrf"],
          field("learner_id","الرقم الداخلي للمستفيد","number",value=learner_id or "",extra='min="1"')+
          choose("category","الفئة القانونية",ERASURE_CATEGORIES)+
          area("reason","مبرر موثق للحذف (لن يُحفظ نصه داخل سجل الحذف)")+
          field("confirmation","اكتب عبارة: حذف نهائي",value=""),
          "تنفيذ الحذف بعد التحقق")
    if learner_id:
        learner=db.get(Learner,learner_id)
        if learner:
            enrollments=affected_enrollments(db,learner.id)
            evidence=shared_evidence_blockers(db,enrollments)
            body+=f'<p>الملف: {e(learner.full_name)} · سنوات التسجيل: {len(enrollments)}'
            body+=f' · مرفقات مشتركة تتطلب مراجعة: {len(evidence)}</p>'
            if evidence:
                body+='<p>العملية ممنوعة حتى تُراجع المرفقات المبيّنة أدناه وتُعالج.</p>'
                for a in evidence[:35]:
                    body+=f'<p>مرفق #{a.id}: <a href="/platform/attachments/{a.id}/download">{e(a.original_name)}</a></p>'
    return page(request,user,"حماية البيانات والحذف النهائي",body+'</section>')

@app.post("/platform/privacy/erase")
def erase_post(request:Request,token:str=Form(alias="_csrf"),learner_id:int=Form(),
               category:str=Form(),reason:str=Form(),confirmation:str=Form(),
               db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin")
    assert_csrf(request,token)
    if confirmation.strip()!="حذف نهائي":
        raise HTTPException(422,"تأكيد الحذف النهائي غير مطابق")
    erase_learner(db,user,learner_id,reason,category)
    return RedirectResponse("/platform/learners",status_code=303)


@app.post("/platform/reports/{report_id}/erase")
def erase_session_report(request:Request,report_id:int,token:str=Form(alias="_csrf"),
                        reason:str=Form(),confirmation:str=Form(),
                        db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin")
    assert_csrf(request,token)
    if confirmation.strip()!="حذف التقرير" or len(reason.strip())<10:
        raise HTTPException(422,"تأكيد الحذف وتعليله إلزاميان")
    report=db.get(SessionReport,report_id)
    if not report:raise HTTPException(404,"التقرير غير موجود")
    attachments=db.scalars(select(Attachment).where(Attachment.report_id==report.id)).all()
    attachment_ids=[item.id for item in attachments]
    if attachment_ids:
        db.execute(delete(AttachmentBlob).where(AttachmentBlob.attachment_id.in_(attachment_ids)))
        db.execute(delete(Attachment).where(Attachment.id.in_(attachment_ids)))
    db.execute(delete(SessionReportProof).where(SessionReportProof.report_id==report.id))
    db.execute(delete(Audit).where(Audit.resource=="session_report",Audit.resource_id==report.id))
    db.execute(delete(Audit).where(Audit.resource=="attachment",
                                  Audit.resource_id.in_(attachment_ids)))
    db.delete(report)
    stamp=hmac.new(APP_SECRET.encode(),reason.strip().encode(),hashlib.sha256).hexdigest()[:16]
    db.add(Audit(actor=user["name"],action="session_report_erasure",
                 resource="privacy",resource_id=0,
                 reason=f"category=report;reason_digest={stamp}"))
    db.commit()
    return RedirectResponse("/platform/reports",status_code=303)
