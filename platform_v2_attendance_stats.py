"""Authorized per-track attendance statistics and audited CSV download.

Counts are operational indicators and are not disciplinary decisions.
Only authorized tracks are shown. Personal CSV exports require admin/coordinator.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import date
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import app, get_db, make_csv
from platform_v2 import actor, by_id, e, log, page, report_filter_box, table, visible_tracks
from platform_v2_models import Attendance, Center, Enrollment, Learner, SessionDay, Slot, Year

MAX_LEARNERS = 800
CSV_HEADERS = [
    "الاسم الكامل", "الرمز الأصلي", "حضور", "تأخر", "غياب بإذن",
    "غياب دون إذن", "حصص غير مسجلة", "نسبة الحضور %",
]


def default_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return today.replace(day=1), today


def attendance_rate(present: int, late: int, absent: int) -> float | None:
    recorded = present + late + absent
    return round((present + late) * 100 / recorded, 1) if recorded else None


def parse_range(start: date | None, end: date | None) -> tuple[date, date]:
    default_start, default_end = default_range()
    start, end = start or default_start, end or default_end
    if start > end:
        raise HTTPException(422, "تاريخ البداية يجب أن يسبق تاريخ النهاية.")
    if (end - start).days > 400:
        raise HTTPException(422, "الفترة المطلوبة أطول من اللازم (الحد الأقصى 400 يوم).")
    return start, end


def selected_track(db: Session, user, track_id: int):
    track = next((t for t in visible_tracks(db, user) if t.id == track_id), None)
    if track is None:
        raise HTTPException(403, "هذا المسلك غير متاح لحسابك.")
    return track


def compute_track_stats(db: Session, track_id: int, start: date, end: date) -> dict:
    """Group all attendance records for a track in bounded fixed-count queries."""
    in_range = (
        Slot.track_id == track_id,
        SessionDay.status != "canceled",
        SessionDay.held_on >= start,
        SessionDay.held_on <= min(end, date.today()),
    )
    session_dates = db.scalars(
        select(SessionDay.held_on).join(Slot, SessionDay.slot_id == Slot.id)
        .where(*in_range).order_by(SessionDay.held_on)
    ).all()
    has_permit = case((Attendance.permit_id.is_(None), 0), else_=1)
    grouped = db.execute(
        select(Attendance.enrollment_id, Attendance.status, has_permit, func.count())
        .join(SessionDay, Attendance.session_id == SessionDay.id)
        .join(Slot, SessionDay.slot_id == Slot.id)
        .where(*in_range)
        .group_by(Attendance.enrollment_id, Attendance.status, has_permit)
    ).all()
    per_learner: dict[int, dict[str, int]] = {}
    for enrollment_id, status, permit, count in grouped:
        row = per_learner.setdefault(enrollment_id, {
            "present": 0, "late": 0, "absent_permit": 0, "absent_open": 0,
        })
        if status == "present":
            row["present"] += count
        elif status == "late":
            row["late"] += count
        elif status == "absent":
            row["absent_permit" if permit else "absent_open"] += count
    return {
        "sessions_held": len(session_dates),
        "session_dates": session_dates,
        "per_learner": per_learner,
    }


def roster_rows(db: Session, track_id: int, stats: dict) -> list[dict]:
    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.track_id == track_id)
        .order_by(Enrollment.id.desc()).limit(MAX_LEARNERS)
    ).all()
    learners = by_id(db, Learner, (en.learner_id for en in enrollments))
    rows = []
    for en in enrollments:
        counts = stats["per_learner"].get(en.id, {
            "present": 0, "late": 0, "absent_permit": 0, "absent_open": 0,
        })
        absent = counts["absent_permit"] + counts["absent_open"]
        recorded = counts["present"] + counts["late"] + absent
        # Sessions before registration must not count as missing for this learner.
        eligible = len(stats["session_dates"]) - bisect_left(
            stats["session_dates"], max(en.enrolled_on, date.min)
        )
        rows.append({
            "enrollment_id": en.id,
            "name": learners[en.learner_id].full_name,
            "code": learners[en.learner_id].source_code or "",
            **counts,
            "unrecorded": max(eligible - recorded, 0),
            "rate": attendance_rate(counts["present"], counts["late"], absent),
        })
    rows.sort(key=lambda row: (-row["absent_open"], row["name"]))
    return rows


def summary_cards(sessions_held: int, rows: list[dict]) -> str:
    totals = {
        key: sum(row[key] for row in rows)
        for key in ("present", "late", "absent_permit", "absent_open")
    }
    overall = attendance_rate(
        totals["present"], totals["late"], totals["absent_permit"] + totals["absent_open"]
    )
    items = [
        ("حصص غير ملغاة في الفترة", sessions_held),
        ("حضور وتأخر", totals["present"] + totals["late"]),
        ("غياب بإذن", totals["absent_permit"]),
        ("غياب دون إذن", totals["absent_open"]),
        ("نسبة الحضور من السجلات", "—" if overall is None else f"{overall}%"),
    ]
    cards = "".join(
        f'<div class="pv2-report-stat"><span>{e(label)}</span>'
        f'<strong>{e(value)}</strong></div>' for label, value in items
    )
    return f'<div class="pv2-report-stats">{cards}</div>'


@app.get("/platform/attendance/stats", response_class=HTMLResponse)
def attendance_stats_page(
    request: Request, track_id: int | None = None,
    start: date | None = None, end: date | None = None,
    db: Session = Depends(get_db), user=Depends(actor),
):
    if track_id is None:
        tracks = visible_tracks(db, user)
        centers = by_id(db, Center, (track.center_id for track in tracks))
        years = by_id(db, Year, (track.year_id for track in tracks))
        links = "".join(
            f'<a class="pv2-track-link" href="/platform/attendance/stats?track_id={track.id}">'
            f'{e(track.name)} <small>({e(centers[track.center_id].name)}'
            f' · {e(years[track.year_id].label)})</small></a>' for track in tracks
        )
        body = (
            '<section class="pv2-roster-intro"><h2>اختيار المسلك</h2>'
            '<p class="subtle">اختر المسلك لعرض إحصائيات الحضور والغياب للمستفيدين المخول لك الاطلاع عليهم.</p>'
            '</section><section class="pv2-track-section"><div class="pv2-track-grid">'
            + (links or '<p class="subtle">لا توجد مسالك متاحة لحسابك.</p>')
            + '</div></section>'
        )
        return page(request, user, "إحصائيات الحضور", body)
    track = selected_track(db, user, track_id)
    start, end = parse_range(start, end)
    stats = compute_track_stats(db, track.id, start, end)
    rows = roster_rows(db, track.id, stats)
    query = urlencode({
        "track_id": track.id, "start": start.isoformat(), "end": end.isoformat(),
    })
    can_export = user["role"] in ("admin", "coordinator")
    filters = (
        '<form method="get" class="form-grid pv2-report-filters no-print" '
        'action="/platform/attendance/stats">'
        f'<input type="hidden" name="track_id" value="{track.id}">'
        f'<label>من<input type="date" name="start" value="{start.isoformat()}" required></label>'
        f'<label>إلى<input type="date" name="end" value="{end.isoformat()}" required></label>'
        '<button type="submit">عرض الإحصائيات</button></form>'
    )
    export = (
        f'<a class="button no-print" href="/platform/attendance/stats.csv?{e(query)}">'
        'تحميل CSV</a>'
    ) if can_export else ""
    table_rows = [[
        e(row["name"]), e(row["code"]) or "—",
        row["present"], row["late"], row["absent_permit"],
        f'<strong>{row["absent_open"]}</strong>' if row["absent_open"] else "0",
        row["unrecorded"],
        "—" if row["rate"] is None else f'{row["rate"]}%',
    ] for row in rows]
    body = (
        f'<section class="pv2-roster-intro"><h2>{e(track.name)}</h2>'
        f'<p class="subtle">الفترة: {start.isoformat()} إلى {end.isoformat()} · '
        f'عدد المستفيدين المعروضين: {len(rows)}. المؤشرات للمتابعة الإدارية فقط.</p>'
        '<a class="button no-print" href="/platform/attendance/stats">تغيير المسلك</a> '
        + export + '</section>'
        + filters + summary_cards(stats["sessions_held"], rows)
        + '<section class="pv2-roster-list"><h2>التفصيل لكل مستفيد</h2>'
        + report_filter_box("attendance-stats-table", "بحث بالاسم أو الرمز")
        + '<div id="attendance-stats-table">'
        + table(
            ["الاسم الكامل", "الرمز الأصلي", "حضور", "تأخر", "غياب بإذن",
             "غياب دون إذن", "غير مسجلة", "نسبة الحضور"],
            table_rows, cards=True,
        )
        + '</div><p class="subtle">النسبة = (حضور + تأخر) ÷ السجلات الموجودة. '
        'الحصص الملغاة والمستقبلية لا تُحتسب، ولا يُحتسب أي تاريخ قبل التسجيل ضمن الحصص غير المسجلة. '
        '«غياب دون إذن» مؤشر للمتابعة، وليس قراراً تأديبياً.</p></section>'
    )
    return page(request, user, "إحصائيات الحضور", body)


@app.get("/platform/attendance/stats.csv")
def attendance_stats_csv(
    track_id: int, start: date | None = None, end: date | None = None,
    db: Session = Depends(get_db), user=Depends(actor),
):
    if user["role"] not in ("admin", "coordinator"):
        raise HTTPException(403, "تصدير البيانات الفردية متاح للإدارة والمنسقين فقط.")
    track = selected_track(db, user, track_id)
    start, end = parse_range(start, end)
    stats = compute_track_stats(db, track.id, start, end)
    rows = roster_rows(db, track.id, stats)
    log(
        db, user, "export", "attendance_stats", track.id,
        f"{start.isoformat()}..{end.isoformat()}"
    )
    db.commit()
    return make_csv(
        f"attendance-track-{track.id}-{start.isoformat()}-{end.isoformat()}.csv",
        CSV_HEADERS,
        [[row["name"], row["code"], row["present"], row["late"],
          row["absent_permit"], row["absent_open"], row["unrecorded"],
          "" if row["rate"] is None else row["rate"]] for row in rows],
    )
