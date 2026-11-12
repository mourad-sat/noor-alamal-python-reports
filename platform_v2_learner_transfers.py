"""Controlled learner track transfer with audit and preserved enrollment identity."""
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, center_allowed, choose, e, form, learner_scope, log, page, redirect, require_role
from platform_v2_models import Attendance, Discipline, Enrollment, Grade, Incident, Learner, Notice, Track


def transfer_targets(db: Session, user, enrollment: Enrollment):
    """Changing tracks within the same center/year only; not a center transfer."""
    from sqlalchemy import select
    tracks = db.scalars(select(Track).where(
        Track.year_id == enrollment.year_id,
        Track.center_id == enrollment.center_id,
        Track.id != enrollment.track_id,
    ).order_by(Track.name)).all()
    return tracks if center_allowed(user, enrollment.center_id) else []


@app.get("/platform/learners/{enrollment_id:int}/transfer", response_class=HTMLResponse)
def learner_transfer_page(enrollment_id: int, request: Request,
                          db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    enrollment = learner_scope(db, user, enrollment_id)
    learner = db.get(Learner, enrollment.learner_id)
    current = db.get(Track, enrollment.track_id)
    targets = transfer_targets(db, user, enrollment)
    if not targets:
        raise HTTPException(409, "لا يوجد مسلك آخر متاح في نفس المركز والسنة")
    body = (
        "<section><h2>نقل المستفيد إلى مسلك آخر</h2>"
        f"<p>المستفيد: {e(learner.full_name)} · المسلك الحالي: {e(current.name)}</p>"
        "<p class='subtle'>الانتقال يغيّر المسلك الحالي فقط ولا يحذف سجلات الحضور "
        "أو الاختبارات أو التقارير القديمة. لا يشمل تغيير المركز أو السنة الدراسية.</p>"
        + form(f"/platform/learners/{enrollment.id}/transfer", request.session["csrf"],
               choose("track_id", "المسلك الجديد", [(t.id, t.name) for t in targets])
               + '<label>سبب النقل<textarea name="reason" required minlength="8" maxlength="1000"></textarea></label>'
               + '<label>تأكيد النقل<select name="confirmation" required>'
                 '<option value="">اختيار التأكيد</option>'
                 '<option value="نعم">نعم، أؤكد نقل المستفيد</option></select></label>',
               "تأكيد نقل المستفيد")
        + "</section>"
    )
    return page(request, user, "نقل المستفيد", body)


@app.post("/platform/learners/{enrollment_id:int}/transfer")
def learner_transfer(enrollment_id: int, request: Request,
                     token: str = Form(alias="_csrf"), track_id: int = Form(),
                     reason: str = Form(), confirmation: str = Form(""),
                     db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    assert_csrf(request, token)
    enrollment = learner_scope(db, user, enrollment_id)
    if confirmation != "نعم" or not 8 <= len(reason.strip()) <= 1000:
        raise HTTPException(422, "تأكيد النقل وسبب واضح إلزاميان")
    target = db.get(Track, track_id)
    if (target is None or target.id == enrollment.track_id
        or target.year_id != enrollment.year_id
        or target.center_id != enrollment.center_id):
        raise HTTPException(422, "اختَر مسلكاً آخر من نفس المركز والسنة الدراسية")
    # Do not reattribute existing educational history to a different track.
    # Historical transfers require effective-dated enrollment support.
    for model in (Attendance, Grade, Incident, Discipline, Notice):
        if db.scalar(select(model.id).where(
            model.enrollment_id == enrollment.id
        ).limit(1)) is not None:
            raise HTTPException(
                409,
                "هذا المستفيد عنده سجلات سابقة؛ لا يمكن نقله قبل تفعيل "
                "نظام يحفظ تاريخ المسلك الأصلي.",
            )
    # The enrollment ID remains unchanged, so all historical foreign keys stay intact.
    previous = enrollment.track_id
    enrollment.track_id = target.id
    log(db, user, "transfer_learner_track", "enrollment", enrollment.id,
        f"from_track_id={previous}; to_track_id={target.id}; reason={reason.strip()}")
    db.commit()
    return redirect(f"/platform/learners/{enrollment.id}")
