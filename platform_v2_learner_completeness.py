"""Read-only learner profile completeness review for authorized administrators."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, center_allowed, e, page, require_role, table
from platform_v2_models import Center, Enrollment, Learner, Track, Year

# A review checklist, not a legal or enrollment eligibility requirement.
CHECKLIST = (
    ("born_on", "تاريخ الازدياد"),
    ("birthplace", "مكان الازدياد"),
    ("phone", "هاتف المستفيد"),
    ("address", "عنوان السكن"),
    ("guardian_name", "اسم ولي الأمر"),
    ("guardian_phone", "هاتف ولي الأمر"),
)
PAGE_SIZE = 100


def missing_profile_fields(learner: Learner) -> list[str]:
    """Report empty optional profile fields without mutating the learner."""
    return [
        label for attribute, label in CHECKLIST
        if not str(getattr(learner, attribute, None) or "").strip()
    ]


@app.get("/platform/profile-completeness", response_class=HTMLResponse)
def learner_completeness(
    request: Request,
    track_id: int | None = None,
    page_number: int = 1,
    db: Session = Depends(get_db),
    user=Depends(actor),
):
    require_role(user, "admin", "coordinator")
    if page_number < 1 or page_number > 100000:
        raise HTTPException(422, "رقم الصفحة غير صالح")

    # Never list personal details across centers or before choosing a track.
    visible_tracks = db.scalars(
        select(Track).order_by(Track.center_id, Track.year_id, Track.name)
    ).all()
    visible_tracks = [t for t in visible_tracks if center_allowed(user, t.center_id)]
    if track_id is None:
        blocks = [
            '<section><h2>مراجعة اكتمال ملفات المستفيدين</h2>'
            '<p class="subtle">اختر المسلك أولاً. لا تظهر أسماء أو معلومات شخصية '
            'قبل اختيار مسلك متاح لحسابك.</p></section>'
        ]
        centers = {c.id: c for c in db.scalars(select(Center)).all()}
        for center_id in sorted({t.center_id for t in visible_tracks}):
            links = "".join(
                f'<a class="button" style="display:inline-block;margin:5px" '
                f'href="/platform/profile-completeness?track_id={t.id}">{e(t.name)}</a>'
                for t in visible_tracks if t.center_id == center_id
            )
            blocks.append(f'<section><h2>{e(centers[center_id].name)}</h2>{links}</section>')
        return page(request, user, "استكمال ملفات المستفيدين", "".join(blocks))

    selected = next((t for t in visible_tracks if t.id == track_id), None)
    if selected is None:
        raise HTTPException(403, "هذا المسلك غير متاح لحسابك.")

    total = db.scalar(
        select(func.count()).select_from(Enrollment)
        .where(Enrollment.track_id == selected.id)
    ) or 0
    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.track_id == selected.id)
        .order_by(Enrollment.id.desc())
        .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    learners = {l.id: l for l in db.scalars(
        select(Learner).where(Learner.id.in_([en.learner_id for en in enrollments]))
    ).all()} if enrollments else {}

    rows = []
    incomplete_count = 0
    for enrollment in enrollments:
        learner = learners.get(enrollment.learner_id)
        if learner is None:
            continue
        missing = missing_profile_fields(learner)
        if missing:
            incomplete_count += 1
        status = ('<span class="good">مستكمل حسب لائحة المراجعة</span>'
                  if not missing else '<span class="danger">يحتاج استكمالاً</span>')
        rows.append([
            e(learner.full_name),
            status,
            e("، ".join(missing)) if missing else "—",
            f'<a href="/platform/learners/{enrollment.id}/edit">استكمال الملف</a>',
        ])
    center = db.get(Center, selected.center_id)
    year = db.get(Year, selected.year_id)
    title = f"{center.name} · {selected.name} · {year.label}"
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page_number > pages:
        raise HTTPException(404, "هذه الصفحة غير موجودة")
    nav = " ".join(
        f'<a class="button" href="/platform/profile-completeness?track_id={selected.id}&page_number={p}">'
        f'{"السابق" if p < page_number else "التالي"}</a>'
        for p in (page_number - 1, page_number + 1) if 1 <= p <= pages
    )
    intro = (
        f'<section><h2>{e(title)}</h2>'
        f'<p>عدد ملفات المسلك: <strong>{total}</strong> · '
        f'الملفات التي تحتاج استكمالاً في هذه الصفحة: <strong>{incomplete_count}</strong> '
        f'من أصل {len(rows)} ملفاً معروضاً.</p>'
        '<p class="subtle">هذه لائحة مراجعة إدارية لحقول اختيارية وليست حكماً '
        'على أهلية المستفيد. لا يتم تعديل أي بيانات تلقائياً.</p>'
        '<a class="button" href="/platform/profile-completeness">اختيار مسلك آخر</a>'
        '</section>'
    )
    body = intro + '<section><h2>الملفات المطلوب مراجعتها</h2>' + table(
        ["المستفيد", "الحالة", "الخانات الناقصة", "الإجراء"], rows
    ) + f'<p class="subtle">الصفحة {page_number} من {pages}</p>{nav}</section>'
    return page(request, user, "استكمال ملفات المستفيدين", body)
