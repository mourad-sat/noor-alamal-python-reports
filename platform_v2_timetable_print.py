"""Printable weekly timetables for staff, centers and all groups.

The A4 layout follows the user-provided timetable reference: framed identity
table, centered heading, weekday/time matrix and two teaching periods.
No timetable data, printed times or learner counts are fabricated.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, by_id, center_allowed, center_options, e
from platform_v2_models import (
    Assignment, Center, Enrollment, Slot, Staff, Track, Year,
)

DAY_NAMES = ("الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
PRINT_MODES = (
    ("center", "جميع المجموعات في مركز واحد"),
    ("all", "جميع المجموعات في المركزين معاً"),
    ("staff", "استعمال الزمن لمكوّن أو منشّط (المركزان أو مركز واحد)"),
    ("track", "استعمال الزمن لمجموعة / مسلك"),
)


def timetable_cells(slots: list[Slot], tracks: dict, centers: dict,
                    staff: dict, mode: str) -> str:
    """Make the two-period day matrix with actual times and recorded assignments."""
    # Sunday only when present in the recurring schedule; reference has Mon–Sat.
    days = range(7 if any(slot.weekday == 6 for slot in slots) else 6)
    columns = []
    for weekday in days:
        day_slots = [slot for slot in slots if slot.weekday == weekday]
        if day_slots:
            first = min(slot.starts_at for slot in day_slots)
            last = max(slot.ends_at for slot in day_slots)
            hours = f'<small>من {e(first)} إلى {e(last)}</small>'
        else:
            hours = '<small>—</small>'
        columns.append(
            f'<th scope="col">{DAY_NAMES[weekday]}<sup>{weekday+1}</sup>{hours}</th>'
        )
    row_html = []
    for period, title in ((0, "الفترة الأولى"), (1, "الفترة الثانية")):
        cells = []
        for weekday in days:
            chosen = sorted(
                (slot for slot in slots if slot.weekday == weekday
                 and (slot.starts_at < "13:00") == (period == 0)),
                key=lambda slot: (slot.starts_at, slot.ends_at, slot.center_id, slot.track_id),
            )
            entries = []
            for slot in chosen:
                label_parts = [f'<span class="print-class">{e(slot.subject)}</span>']
                label_parts.append(
                    f'<span class="print-time" dir="ltr">{e(slot.starts_at)}–{e(slot.ends_at)}</span>'
                )
                if mode in ("all", "staff"):
                    label_parts.append(f'<span>{e(centers[slot.center_id].name)}</span>')
                if mode != "track":
                    label_parts.append(f'<span>{e(tracks[slot.track_id].name)}</span>')
                if mode != "staff":
                    label_parts.append(f'<span>{e(staff[slot.staff_id].full_name)}</span>')
                entries.append('<div class="print-lesson">' + "".join(label_parts) + '</div>')
            cells.append('<td>' + ("".join(entries) if entries else
                                   '<span class="print-blank">—</span>') + '</td>')
        row_html.append(
            f'<tr class="print-period print-period-{period+1}">'
            f'<th scope="row">{title}</th>' + "".join(cells) + '</tr>'
        )
    return (
        '<table class="print-week" dir="rtl">'
        '<thead><tr><th scope="col" class="print-corner">الأيام / التوقيت</th>'
        + "".join(columns) + '</tr></thead><tbody>'
        + "".join(row_html) + '</tbody></table>'
    )


def print_document(*, title: str, institution: str, year_label: str,
                   track_label: str, specialization: str, learner_count: int,
                   level: str, slots: list[Slot], tracks: dict,
                   centers: dict, staff: dict, mode: str, controls: str) -> str:
    matrix = timetable_cells(slots, tracks, centers, staff, mode)
    return (
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>استعمال الزمن — جمعية نور الأمل</title>'
        '<link rel="stylesheet" href="/static/timetable_print.css">'
        '<script defer src="/static/platform_ui.js"></script>'
        '<script defer src="/static/timetable_print.js"></script>'
        '</head><body>'
        '<div class="print-controls no-print">'
        '<div class="print-control-heading"><strong>طباعة استعمال الزمن</strong>'
        '<a href="/platform/schedule">العودة إلى الجدولة</a></div>'
        + controls
        + '<button type="button" data-print-timetable>طباعة / حفظ PDF</button>'
        '<p>الطباعة وفق النموذج المرفق على ورق A4 عمودي. '
        'البيانات مأخوذة من الحصص الأسبوعية المسجّلة فقط.</p>'
        '</div>'
        '<main class="print-paper">'
        '<div class="print-frame">'
        '<table class="print-identity" aria-label="معلومات استعمال الزمن"><tbody>'
        f'<tr><th>المؤسسة:</th><td>{e(institution)}</td>'
        f'<th>المستوى:</th><td>{e(level)}</td></tr>'
        f'<tr><th>الفوج:</th><td>{e(track_label)}</td>'
        f'<th>التخصص:</th><td>{e(specialization)}</td></tr>'
        f'<tr><th>عدد التلاميذ:</th><td>{learner_count}</td>'
        f'<th>السنة الدراسية:</th><td>{e(year_label)}</td></tr>'
        '</tbody></table>'
        f'<h1>استعمال الزمن</h1><h2>(فترة التدريس الصريح)</h2>'
        f'<p class="print-subtitle">{e(title)}</p>'
        + matrix
        + '<p class="print-footer">جمعية نور الأمل · يعتمد الجدول على '
        'الحصص المتكررة النشطة؛ لا تُدرج الحصص الاستثنائية أو المؤرشفة.</p>'
        '</div></main></body></html>'
    )


def select_options(values, selected, *, empty_label=None):
    result = []
    if empty_label is not None:
        result.append('<option value="0"' +
                      (' selected' if selected in (None, 0) else '') +
                      f'>{e(empty_label)}</option>')
    result.extend(
        f'<option value="{e(value)}"' +
        (' selected' if value == selected else '') +
        f'>{e(label)}</option>'
        for value, label in values
    )
    return "".join(result)


@app.get("/platform/schedule/print", response_class=HTMLResponse)
def print_timetable(request: Request, mode: str | None = None,
                    center_id: int = 0, staff_id: int = 0,
                    track_id: int = 0, year_id: int = 0,
                    db: Session = Depends(get_db), user=Depends(actor)):
    """Printable combinations. Scope all filters server-side, never only in UI."""
    if mode is None:
        mode = "staff" if user["role"] in ("educator", "instructor") else "center"
    if mode not in dict(PRINT_MODES):
        raise HTTPException(422, "نوع استعمال الزمن غير صحيح")
    allowed_center_pairs = center_options(db, user)
    allowed = {cid for cid, _ in allowed_center_pairs}
    if mode == "all" and user["role"] != "admin":
        raise HTTPException(403, "الجدول الإجمالي للمركزين من صلاحيات الإدارة")
    if center_id and center_id not in allowed:
        raise HTTPException(403, "لا يحق لك الاطلاع على هذا المركز")
    if mode == "center" and center_id == 0 and allowed_center_pairs:
        center_id = allowed_center_pairs[0][0]

    years = db.scalars(select(Year).order_by(Year.starts_on.desc(), Year.id.desc())).all()
    if year_id:
        year = next((y for y in years if y.id == year_id), None)
        if year is None:
            raise HTTPException(404, "السنة الدراسية غير موجودة")
    else:
        year = next((y for y in years if y.active and not y.archived), None)
        if year is None:
            year = next((y for y in years if not y.archived), years[0] if years else None)

    available_tracks = db.scalars(
        select(Track).where(Track.center_id.in_(allowed)).order_by(Track.center_id, Track.name)
    ).all() if allowed else []
    tracks_by_id = {track.id: track for track in available_tracks}
    workers = db.scalars(
        select(Staff).join(Assignment, Assignment.staff_id == Staff.id)
        .where(Assignment.center_id.in_(allowed), Staff.enabled.is_(True),
               Staff.role.in_(("instructor", "educator")))
        .distinct().order_by(Staff.full_name)
    ).all() if allowed else []
    if user["role"] in ("instructor", "educator"):
        workers = [person for person in workers if person.id == user["id"]]
        available_tracks = [track for track in available_tracks if any(
            assignment.track_id == track.id for assignment in user["assignments"]
        )]
        tracks_by_id = {track.id: track for track in available_tracks}
    worker_by_id = {staff.id: staff for staff in workers}
    if user["role"] in ("instructor", "educator"):
        if mode != "staff" or staff_id not in (0, user["id"]):
            raise HTTPException(403, "يمكنك طباعة استعمال الزمن الخاص بك فقط")
        staff_id = user["id"]
    if mode == "staff":
        if staff_id == 0 and workers:
            staff_id = user["id"] if user["role"] in ("instructor", "educator") else workers[0].id
        if staff_id not in worker_by_id:
            raise HTTPException(403, "هذا الموظف غير متاح ضمن نطاق صلاحياتك")
    if mode == "track":
        if track_id == 0 and available_tracks:
            track_id = available_tracks[0].id
        if track_id not in tracks_by_id:
            raise HTTPException(403, "هذا المسلك غير متاح ضمن نطاق صلاحياتك")
        if center_id and tracks_by_id[track_id].center_id != center_id:
            raise HTTPException(422, "المسلك لا ينتمي إلى المركز المختار")

    target_centers = sorted(allowed)
    if mode == "center":
        target_centers = [center_id] if center_id in allowed else []
    elif mode == "track":
        target_centers = [tracks_by_id[track_id].center_id]
    elif mode == "staff" and center_id:
        target_centers = [center_id]

    if year is not None and target_centers:
        query = select(Slot).where(
            Slot.center_id.in_(target_centers), Slot.year_id == year.id,
            Slot.canceled.is_(False), Slot.specific_date.is_(None),
        )
        if mode == "staff":
            query = query.where(Slot.staff_id == staff_id)
        elif mode == "track":
            query = query.where(Slot.track_id == track_id)
        slots = db.scalars(query.order_by(Slot.weekday, Slot.starts_at, Slot.center_id)).all()
    else:
        slots = []
    if user["role"] in ("instructor", "educator"):
        slots = [slot for slot in slots if slot.staff_id == user["id"]]
    if user["role"] == "coordinator":
        slots = [slot for slot in slots if slot.center_id in allowed]

    # Scope metadata to the selected view, not to unrelated organizations or years.
    chosen_tracks = {slot.track_id for slot in slots}
    if mode == "track":
        chosen_tracks.add(track_id)
    elif mode == "center" and center_id:
        chosen_tracks.update(t.id for t in available_tracks
                             if t.center_id == center_id and (year is None or t.year_id == year.id))
    elif mode == "all":
        chosen_tracks.update(t.id for t in available_tracks
                             if year is None or t.year_id == year.id)
    enroll_count = db.scalar(
        select(func.count(Enrollment.id)).where(
            Enrollment.track_id.in_(chosen_tracks),
            Enrollment.status == "active",
            *([Enrollment.year_id == year.id] if year else []),
        )
    ) or 0 if chosen_tracks else 0

    centers_by_id = by_id(db, Center, target_centers)
    track_refs = by_id(db, Track, (slot.track_id for slot in slots))
    staff_refs = by_id(db, Staff, (slot.staff_id for slot in slots))
    if mode == "staff":
        person = worker_by_id[staff_id]
        title = f"استعمال الزمن الشخصي — {person.full_name}"
        specialization = "التكوين المهني" if person.role == "instructor" else "التأهيل التربوي"
        institution = " / ".join(centers_by_id[cid].name for cid in target_centers if cid in centers_by_id)
        group_label = "المجموعات المسندة" if len(chosen_tracks) != 1 else (
            tracks_by_id[next(iter(chosen_tracks))].name)
    elif mode == "track":
        track = tracks_by_id[track_id]
        title = f"استعمال الزمن للمجموعة — {track.name}"
        institution = centers_by_id[track.center_id].name
        specialization = "حسب الحصص المسجلة"
        group_label = track.name
    elif mode == "all":
        title = "استعمال الزمن الإجمالي لجميع مجموعات المركزين"
        institution = "جمعية نور الأمل — جميع المراكز"
        specialization = "جميع التخصصات"
        group_label = "جميع المجموعات"
    else:
        title = "استعمال الزمن الإجمالي — " + (centers_by_id[center_id].name if center_id in centers_by_id else "المركز")
        institution = centers_by_id[center_id].name if center_id in centers_by_id else "جمعية نور الأمل"
        specialization = "جميع التخصصات"
        group_label = "جميع مجموعات المركز"
    if mode == "staff" and not center_id:
        institution = " / ".join(centers_by_id[cid].name for cid in target_centers if cid in centers_by_id)

    center_opts = select_options(allowed_center_pairs, center_id, empty_label="جميع المراكز المتاحة")
    worker_opts = select_options(
        [(s.id, s.full_name + (" — مكوّن" if s.role == "instructor" else " — منشّط"))
         for s in workers], staff_id,
    )
    track_opts = select_options(
        [(t.id, f"{t.name} — " +
          next((name for cid, name in allowed_center_pairs if cid == t.center_id), ""))
         for t in available_tracks], track_id,
    )
    mode_opts = "".join(
        f'<option value="{code}"' + (' selected' if code == mode else '') +
        f'>{e(label)}</option>' for code, label in PRINT_MODES
        if user["role"] == "admin" or code != "all"
        if user["role"] in ("admin", "coordinator") or code == "staff"
    )
    controls = (
        '<form class="print-picker" action="/platform/schedule/print" method="get">'
        '<label>نوع الاستعمال<select name="mode">' + mode_opts + '</select></label>'
        '<label data-print-filter="center">المركز<select name="center_id">' + center_opts + '</select></label>'
        '<label data-print-filter="staff">المكوّن / المنشّط<select name="staff_id">' + worker_opts + '</select></label>'
        '<label data-print-filter="track">المجموعة / المسلك<select name="track_id">' + track_opts + '</select></label>'
        '<label>السنة الدراسية<select name="year_id">'
        + select_options([(y.id, y.label) for y in years], year.id if year else 0)
        + '</select></label><button type="submit">عرض استعمال الزمن</button>'
        '</form>'
    )
    return HTMLResponse(
        print_document(
            title=title, institution=institution, level="الفرصة الثانية – الجيل الجديد",
            year_label=year.label if year else "غير محددة",
            track_label=group_label, specialization=specialization,
            learner_count=enroll_count, slots=slots,
            tracks=track_refs, centers=centers_by_id,
            staff=staff_refs, mode=mode, controls=controls,
        ),
        headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"},
    )
