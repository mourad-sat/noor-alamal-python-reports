"""Approved-only weekly/monthly aggregated reporting."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from urllib.parse import urlencode
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db, make_csv
from platform_v2 import actor, center_allowed, e, page, table
from platform_v2_models import Attachment, Attendance, Center, Enrollment, SessionDay, SessionReport, Slot, Track, Year

def period_bounds(period:str,anchor:date):
    if period=="weekly":
        start=anchor-timedelta(days=anchor.weekday())
        return start,start+timedelta(days=6)
    if period=="monthly":
        start=anchor.replace(day=1)
        next_month=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
        return start,next_month-timedelta(days=1)
    raise HTTPException(422,"نوع الفترة غير صحيح")

def approved_stats(db:Session,slots:list[Slot],start:date,end:date):
    # Scope all reads to visible slot IDs and the requested period in SQL.
    # An approved report is required; a session with multiple matching reports
    # must still be counted only once.
    ids={s.id for s in slots}
    if not ids:
        return dict(sessions=0, recorded=0, present=0, absent=0,
                    absent_days=0, missing=0)
    approved_session_ids = select(SessionReport.session_id).where(
        SessionReport.status == "approved"
    )
    selected=db.scalars(
        select(SessionDay).where(
            SessionDay.slot_id.in_(ids),
            SessionDay.held_on >= start,
            SessionDay.held_on <= end,
            SessionDay.status != "canceled",
            SessionDay.id.in_(approved_session_ids),
        )
    ).all()
    if not selected:
        return dict(sessions=0, recorded=0, present=0, absent=0,
                    absent_days=0, missing=0)
    session_ids=[s.id for s in selected]
    att=db.scalars(select(Attendance).where(Attendance.session_id.in_(session_ids))).all()
    session_dates={s.id:s.held_on for s in selected}
    recorded_session_ids={a.session_id for a in att}
    absent=[a for a in att if a.status=="absent"]
    present=[a for a in att if a.status in ("present","late")]
    return dict(sessions=len(selected),recorded=len(recorded_session_ids),
        present=len(present),absent=len(absent),
        absent_days=len({(a.enrollment_id,session_dates[a.session_id]) for a in absent}),
        missing=len(selected)-len(recorded_session_ids))

def stats_html(stats):
    """Accessible summary cards with text values (not visual charts alone)."""
    values = [
        ("الحصص المعتمدة", stats["sessions"], "sessions"),
        ("حصص مسجلة الحضور", stats["recorded"], "recorded"),
        ("الحضور والتأخر", stats["present"], "present"),
        ("حصص الغياب", stats["absent"], "absent"),
        ("أيام الغياب لكل مستفيد", stats["absent_days"], "days"),
    ]
    if stats["missing"]:
        values.append(("حصص دون سجل حضور", stats["missing"], "missing"))
    cards = "".join(
        f'<div class="pv2-report-stat pv2-report-stat--{kind}">'
        f'<span>{e(label)}</span><strong>{number}</strong></div>'
        for label, number, kind in values
    )
    return f'<div class="pv2-report-stats">{cards}</div>'


def attendance_visual(stats):
    """Display relative attendance records while retaining exact counts in text."""
    present = stats["present"]
    absent = stats["absent"]
    total = present + absent
    if not total:
        return '<p class="subtle">لا توجد سجلات حضور أو غياب ضمن التقارير المعتمدة لهذه الفترة.</p>'
    percent = round(present * 100 / total, 1)
    return (
        '<div class="pv2-attendance-visual">'
        f'<div class="pv2-attendance-caption"><strong>توزيع سجلات الحضور والغياب</strong>'
        f'<span>الحضور والتأخر: {present} · الغياب: {absent}</span></div>'
        f'<div class="pv2-attendance-track" role="img" '
        f'aria-label="حضور وتأخر: {present}، غياب: {absent}، نسبة الحضور من سجلات الحضور والغياب {percent} بالمائة">'
        f'<span class="pv2-attendance-present" style="width:{percent}%"></span></div>'
        '<div class="pv2-attendance-legend">'
        '<span><i class="pv2-legend-dot pv2-legend-dot--present"></i>حضور وتأخر</span>'
        '<span><i class="pv2-legend-dot pv2-legend-dot--absent"></i>غياب</span>'
        '</div><p class="subtle">النسبة محسوبة على سجلات الحضور والغياب فقط، '
        'ولا تمثل نسبة حضور المستفيدين الفريدين.</p></div>'
    )


def report_context(db, user, period, anchor, year_id):
    anchor = anchor or date.today()
    start, end = period_bounds(period, anchor)
    years = db.scalars(select(Year).order_by(Year.starts_on.desc())).all()
    selected_year = year_id or next((y.id for y in years if y.active), None) or (
        years[0].id if years else None
    )
    if selected_year and not any(y.id == selected_year for y in years):
        raise HTTPException(404, "السنة غير موجودة")
    all_tracks = db.scalars(select(Track).where(Track.year_id == selected_year)).all() if selected_year else []
    all_slots = db.scalars(select(Slot).where(
        Slot.year_id == selected_year, Slot.canceled.is_(False)
    )).all() if selected_year else []
    centers = db.scalars(select(Center).order_by(Center.id)).all()
    available = []
    for center in centers:
        if not center_allowed(user, center.id):
            continue
        tracks = [t for t in all_tracks if t.center_id == center.id]
        if user["role"] in ("instructor", "educator"):
            allowed = {a.track_id for a in user["assignments"]}
            tracks = [t for t in tracks if t.id in allowed]
        if not tracks:
            continue
        selected_tracks = {t.id for t in tracks}
        slots = [slot for slot in all_slots if slot.track_id in selected_tracks]
        available.append((center, tracks, slots))
    return anchor, start, end, years, selected_year, available


@app.get("/platform/aggregates", response_class=HTMLResponse)
def aggregate_page(request: Request, period: str = "weekly", anchor: date | None = None,
                   year_id: int | None = None, db: Session = Depends(get_db),
                   user=Depends(actor)):
    anchor, start, end, years, selected_year, available = report_context(
        db, user, period, anchor, year_id
    )
    download_query = urlencode({
        "period": period, "anchor": anchor.isoformat(), "year_id": selected_year or ""
    })
    options = "".join(
        f'<option value="{y.id}" {"selected" if y.id == selected_year else ""}>{e(y.label)}</option>'
        for y in years
    )
    form_html = (
        '<form action="/platform/aggregates" method="get" class="pv2-report-filters form-grid">'
        '<label>نوع الفترة<select name="period">'
        f'<option value="weekly" {"selected" if period == "weekly" else ""}>أسبوعي</option>'
        f'<option value="monthly" {"selected" if period == "monthly" else ""}>شهري</option>'
        '</select></label>'
        f'<label>التاريخ المرجعي<input type="date" name="anchor" value="{anchor.isoformat()}"></label>'
        f'<label>السنة التكوينية<select name="year_id">{options}</select></label>'
        '<button type="submit">عرض الإحصائيات</button></form>'
    )
    toolbar = (
        '<div class="pv2-report-actions no-print">'
        '<button type="button" onclick="window.print()" aria-label="طباعة التقرير أو حفظه PDF">'
        'طباعة / حفظ PDF</button>'
        f'<a class="button pv2-report-download" href="/platform/aggregates.csv?{download_query}">'
        'تحميل Excel (CSV)</a></div>'
    )
    body = (
        '<section class="pv2-report-intro">'
        '<div class="pv2-report-head"><div><p class="pv2-report-eyebrow">تقارير الأنشطة المعتمدة</p>'
        '<h2>متابعة الأداء الأسبوعي والشهري</h2>'
        f'<p>الفترة من <strong>{start.isoformat()}</strong> إلى <strong>{end.isoformat()}</strong></p>'
        '</div></div>'
        f'{form_html}{toolbar}'
        '<p class="subtle">تعتمد المؤشرات فقط على تقارير الحصص المصادق عليها؛ '
        'التقرير لا يغيّر سجلات الحضور أو بيانات المستفيدين.</p></section>'
    )
    for center, tracks, slots in available:
        stat = approved_stats(db, slots, start, end)
        body += (
            '<section class="pv2-report-section">'
            f'<h2>مركز: {e(center.name)}</h2>'
            + stats_html(stat) + attendance_visual(stat) + '</section>'
        )
        for track in tracks:
            relevant = [item for item in slots if item.track_id == track.id]
            track_stat = approved_stats(db, relevant, start, end)
            body += (
                '<section class="pv2-report-section pv2-report-track">'
                f'<h2>المسلك: {e(track.name)}</h2>'
                + stats_html(track_stat) + attendance_visual(track_stat) + '</section>'
            )
        if user["role"] in ("admin", "coordinator"):
            selected_evidence = []
            eligible_slot_ids = {slot.id for slot in slots}
            for evidence in db.scalars(select(Attachment).where(
                Attachment.select_for_summary.is_(True)
            )).all():
                report = db.get(SessionReport, evidence.report_id)
                if not report or report.status != "approved":
                    continue
                session = db.get(SessionDay, report.session_id)
                if (session and start <= session.held_on <= end
                        and session.slot_id in eligible_slot_ids):
                    selected_evidence.append(evidence)
            if selected_evidence:
                body += '<section class="pv2-report-section"><h2>مرفقات مختارة من المنسق</h2>'
                documents = []
                for evidence in selected_evidence:
                    link = f'/platform/attachments/{evidence.id}/download'
                    if evidence.content_type.startswith("image/"):
                        body += (
                            f'<figure><img alt="{e(evidence.original_name)}" '
                            f'src="/platform/attachments/{evidence.id}/view" '
                            'style="max-width:95%;max-height:260px">'
                            f'<figcaption>{e(evidence.original_name)}</figcaption></figure>'
                        )
                    else:
                        documents.append(f'<li><a href="{link}">{e(evidence.original_name)}</a></li>')
                if documents:
                    body += '<h3>الوثائق المرفقة في الملحق</h3><ul>' + "".join(documents) + '</ul>'
                body += '</section>'
    if user["role"] == "admin" and available:
        combined = [slot for _, _, slots in available for slot in slots]
        overall = approved_stats(db, combined, start, end)
        body += ('<section class="pv2-report-section">'
                 '<h2>إجمالي الجمعية (دون مقارنة بين المركزين)</h2>'
                 + stats_html(overall) + attendance_visual(overall) + '</section>')
    if not available:
        body += '<section><p>لا توجد مسالك ضمن النطاق المتاح لهذه السنة.</p></section>'
    return page(request, user, "التقارير الأسبوعية والشهرية", body)


@app.get("/platform/aggregates.csv")
def aggregate_csv(period: str = "weekly", anchor: date | None = None,
                  year_id: int | None = None, db: Session = Depends(get_db),
                  user=Depends(actor)):
    """Export aggregate-only statistics; apply exactly the same role scope as HTML."""
    _, start, end, _, _, available = report_context(db, user, period, anchor, year_id)
    headers = [
        "المركز", "المسلك", "من", "إلى", "الحصص المعتمدة",
        "حصص مسجلة الحضور", "الحضور والتأخر", "حصص الغياب",
        "أيام الغياب لكل مستفيد", "حصص دون سجل حضور",
    ]
    def row(center_name, track_name, stats):
        return [
            center_name, track_name, start.isoformat(), end.isoformat(),
            stats["sessions"], stats["recorded"], stats["present"],
            stats["absent"], stats["absent_days"], stats["missing"],
        ]
    rows = []
    for center, tracks, slots in available:
        rows.append(row(center.name, "إجمالي المركز", approved_stats(db, slots, start, end)))
        for track in tracks:
            relevant = [slot for slot in slots if slot.track_id == track.id]
            rows.append(row(center.name, track.name, approved_stats(db, relevant, start, end)))
    if user["role"] == "admin" and available:
        combined = [slot for _, _, slots in available for slot in slots]
        rows.append(row("إجمالي الجمعية", "جميع المسالك", approved_stats(db, combined, start, end)))
    return make_csv(f"noor-aggregates-{period}-{start.isoformat()}.csv", headers, rows)
