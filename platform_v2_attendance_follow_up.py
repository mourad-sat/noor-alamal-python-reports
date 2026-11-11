"""Read-only pending absence review scoped to each center and track."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, center_allowed, e, page, require_role, table
from platform_v2_models import Attendance, Center, Enrollment, Learner, SessionDay, Track, Year

PAGE_SIZE = 100


def pending_absences_query(track_id: int):
    """Count only absent attendance rows without an associated entry permit."""
    return (
        select(
            Enrollment.id.label("enrollment_id"),
            Enrollment.learner_id.label("learner_id"),
            func.count(Attendance.id).label("pending_count"),
            func.max(SessionDay.held_on).label("latest_date"),
        )
        .join(Attendance, Attendance.enrollment_id == Enrollment.id)
        .join(SessionDay, SessionDay.id == Attendance.session_id)
        .where(
            Enrollment.track_id == track_id,
            Attendance.status == "absent",
            Attendance.permit_id.is_(None),
        )
        .group_by(Enrollment.id, Enrollment.learner_id)
    )


@app.get("/platform/attendance/follow-up", response_class=HTMLResponse)
def attendance_follow_up(
    request: Request,
    track_id: int | None = None,
    page_number: int = 1,
    db: Session = Depends(get_db),
    user=Depends(actor),
):
    require_role(user, "admin", "coordinator")
    if not 1 <= page_number <= 100000:
        raise HTTPException(422, "رقم الصفحة غير صالح")

    visible_tracks = [
        t for t in db.scalars(
            select(Track).order_by(Track.center_id, Track.year_id, Track.name)
        ).all() if center_allowed(user, t.center_id)
    ]
    if track_id is None:
        centers = {c.id: c for c in db.scalars(select(Center)).all()}
        sections = [
            '<section><h2>متابعة الغياب المعلّق</h2>'
            '<p class="subtle">اختر المركز والمسلك لعرض ملفات الغياب التي لا ترتبط '
            'برخصة دخول. لا تظهر بيانات المستفيدين قبل الاختيار.</p></section>'
        ]
        for center_id in sorted({t.center_id for t in visible_tracks}):
            links = "".join(
                f'<a class="button" style="display:inline-block;margin:5px" '
                f'href="/platform/attendance/follow-up?track_id={t.id}">{e(t.name)}</a>'
                for t in visible_tracks if t.center_id == center_id
            )
            sections.append(f'<section><h2>{e(centers[center_id].name)}</h2>{links}</section>')
        return page(request, user, "متابعة الغياب المعلق", "".join(sections))

    selected = next((t for t in visible_tracks if t.id == track_id), None)
    if selected is None:
        raise HTTPException(403, "هذا المسلك غير متاح لحسابك.")

    pending = pending_absences_query(selected.id).subquery()
    total = db.scalar(select(func.count()).select_from(pending)) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page_number > pages:
        raise HTTPException(404, "هذه الصفحة غير موجودة")
    records = db.execute(
        select(pending.c.enrollment_id, pending.c.learner_id,
               pending.c.pending_count, pending.c.latest_date)
        .order_by(pending.c.pending_count.desc(), pending.c.enrollment_id.desc())
        .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    learners = {l.id: l for l in db.scalars(
        select(Learner).where(Learner.id.in_([r.learner_id for r in records]))
    ).all()} if records else {}
    rows = [
        [e(learners[r.learner_id].full_name), str(r.pending_count),
         e(r.latest_date), f'<a href="/platform/learners/{r.enrollment_id}">فتح الملف</a>']
        for r in records if r.learner_id in learners
    ]
    center = db.get(Center, selected.center_id)
    year = db.get(Year, selected.year_id)
    navigation = " ".join(
        f'<a class="button" href="/platform/attendance/follow-up?track_id={selected.id}&page_number={p}">'
        f'{"السابق" if p < page_number else "التالي"}</a>'
        for p in (page_number - 1, page_number + 1) if 1 <= p <= pages
    )
    intro = (
        f'<section><h2>{e(center.name)} · {e(selected.name)} · {e(year.label)}</h2>'
        f'<p>ملفات بها غياب معلّق: <strong>{total}</strong></p>'
        '<p class="subtle">حصة الغياب المعلّقة هنا هي حصة سُجل فيها المستفيد غائباً '
        'ولم ترتبط برخصة دخول. هذه شاشة متابعة وليست قراراً تأديبياً.</p>'
        '<a class="button" href="/platform/attendance/follow-up">اختيار مسلك آخر</a>'
        '</section>'
    )
    body = (intro + '<section><h2>الملفات المطلوب تتبعها</h2>' +
            table(["المستفيد", "حصص الغياب المعلقة", "تاريخ آخر حصة", "الإجراء"], rows) +
            f'<p class="subtle">الصفحة {page_number} من {pages}</p>{navigation}</section>')
    return page(request, user, "متابعة الغياب المعلق", body)
