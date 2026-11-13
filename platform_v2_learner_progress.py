"""Privacy-scoped individual learner progress card; read-only, print-ready.

Attendance is drawn from this enrollment's actual sessions, with canceled
sessions excluded. Grades are approved-only. Neither source is modified.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, e, learner_scope, page
from platform_v2_models import (Assessment, Attendance, Center, Enrollment,
                                Grade, Learner, SessionDay, Slot, Track, Year)


def progress_data(db: Session, enrollment: Enrollment, start: date, end: date):
    attendance = db.execute(
        select(Attendance, SessionDay, Slot)
        .join(SessionDay, Attendance.session_id == SessionDay.id)
        .join(Slot, SessionDay.slot_id == Slot.id)
        .where(
            Attendance.enrollment_id == enrollment.id,
            SessionDay.held_on >= start,
            SessionDay.held_on <= end,
            SessionDay.status != "canceled",
            Slot.center_id == enrollment.center_id,
            Slot.track_id == enrollment.track_id,
            Slot.year_id == enrollment.year_id,
        )
        .order_by(SessionDay.held_on.desc(), Attendance.id.desc())
    ).all()
    grades = db.execute(
        select(Grade, Assessment)
        .join(Assessment, Grade.assessment_id == Assessment.id)
        .where(
            Grade.enrollment_id == enrollment.id,
            Grade.status == "approved",
            Assessment.track_id == enrollment.track_id,
            Assessment.held_on >= start,
            Assessment.held_on <= end,
        )
        .order_by(Assessment.held_on.desc(), Assessment.id.desc())
    ).all()
    totals = {
        "present": sum(item.status == "present" for item, _, _ in attendance),
        "late": sum(item.status == "late" for item, _, _ in attendance),
        "absent": sum(item.status == "absent" for item, _, _ in attendance),
        "excused": sum(item.status == "absent" and item.permit_id is not None
                       for item, _, _ in attendance),
        "pending": sum(item.status == "absent" and item.permit_id is None
                       for item, _, _ in attendance),
    }
    counted = totals["present"] + totals["late"] + totals["absent"]
    totals["rate"] = (
        round((totals["present"] + totals["late"]) * 100 / counted, 1)
        if counted else None
    )
    monthly = defaultdict(lambda: {"present": 0, "late": 0, "absent": 0})
    for item, day, _ in attendance:
        if item.status in ("present", "late", "absent"):
            monthly[day.held_on.strftime("%Y-%m")][item.status] += 1

    subjects = defaultdict(list)
    grade_monthly = defaultdict(list)
    for item, assessment in grades:
        if assessment.coefficient and assessment.coefficient > 0:
            subjects[assessment.subject].append(
                (Decimal(item.score), Decimal(assessment.coefficient))
            )
            grade_monthly[assessment.held_on.strftime("%Y-%m")].append(
                (Decimal(item.score), Decimal(assessment.coefficient))
            )

    def weighted(rows):
        if not rows:
            return None
        denominator = sum((weight for _, weight in rows), Decimal("0"))
        if denominator <= 0:
            return None
        result = sum((score * weight for score, weight in rows), Decimal("0"))
        return (result / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    return {
        "attendance": attendance,
        "grades": grades,
        "totals": totals,
        "monthly": dict(sorted(monthly.items())),
        "subjects": {name: (weighted(rows), len(rows))
                     for name, rows in sorted(subjects.items())},
        "grade_monthly": {month: weighted(rows)
                          for month, rows in sorted(grade_monthly.items())},
    }


def validate_range(db: Session, enrollment: Enrollment,
                   start: date | None, end: date | None):
    year = db.get(Year, enrollment.year_id)
    low = start or year.starts_on
    high = end or min(year.ends_on, date.today())
    if low > high or low < year.starts_on or high > year.ends_on:
        raise HTTPException(422, "الفترة خارج السنة التكوينية أو تاريخ البداية بعد النهاية")
    return year, low, high


def progress_sections(data):
    t = data["totals"]
    labels = [
        ("حضور", t["present"]),
        ("تأخر", t["late"]),
        ("غياب", t["absent"]),
        ("غياب بإذن", t["excused"]),
        ("غياب دون إذن", t["pending"]),
    ]
    cards = "".join(
        f'<div class="pc-kpi"><span>{e(label)}</span><strong>{number}</strong></div>'
        for label, number in labels
    )
    pct = (f'{t["rate"]}%' if t["rate"] is not None else "غير متاح")
    cards += (
        '<div class="pc-kpi"><span>نسبة الحضور من السجلات</span>'
        f'<strong>{pct}</strong></div>'
    )
    monthly_lines = []
    for month, values in data["monthly"].items():
        count = sum(values.values())
        attended = values["present"] + values["late"]
        rate = round(100 * attended / count, 1) if count else 0
        monthly_lines.append(
            '<div class="pc-month-row">'
            f'<span>{e(month)}</span>'
            f'<div class="pc-progress" role="img" aria-label="'
            f'حضور وتأخر {attended} من {count} خلال {e(month)}">'
            f'<div class="pc-progress-fill" style="width:{rate}%"></div></div>'
            f'<span>{attended}/{count} ({rate}%)</span>'
            '</div>'
        )
    month_chart = "".join(monthly_lines) or (
        '<p class="pc-empty">لا توجد سجلات حضور وغياب للفترة المختارة.</p>'
    )
    subject_rows = "".join(
        '<tr><td>' + e(subject) + '</td>'
        f'<td>{avg if avg is not None else "—"} / 20</td>'
        f'<td>{number}</td></tr>'
        for subject, (avg, number) in data["subjects"].items()
    ) or '<tr><td colspan="3">لا توجد نقط مصادق عليها في هذه الفترة.</td></tr>'
    grade_bars = "".join(
        '<div class="pc-month-row">'
        f'<span>{e(month)}</span>'
        f'<div class="pc-progress" role="img" '
        f'aria-label="معدل النقط المعتمدة {avg} من 20 خلال {e(month)}">'
        f'<div class="pc-progress-grade" style="width:{round(float(avg) * 5, 1)}%"></div></div>'
        f'<span>{avg} / 20</span>'
        '</div>'
        for month, avg in data["grade_monthly"].items() if avg is not None
    ) or '<p class="pc-empty">لا توجد نقط مصادق عليها لإظهار التطور.</p>'
    attendance_rows = "".join(
        '<tr>'
        f'<td>{e(day.held_on.isoformat())}</td>'
        f'<td>{e(slot.subject)}</td>'
        f'<td>{e({"present": "حاضر", "late": "متأخر", "absent": "غائب"}.get(item.status, item.status))}</td>'
        f'<td>{"بإذن" if item.status == "absent" and item.permit_id else "دون إذن" if item.status == "absent" else "—"}</td>'
        '</tr>'
        for item, day, slot in data["attendance"]
    ) or '<tr><td colspan="4">لا توجد سجلات خلال الفترة.</td></tr>'
    grades_rows = "".join(
        '<tr>'
        f'<td>{e(assessment.held_on.isoformat())}</td>'
        f'<td>{e(assessment.subject)}</td>'
        f'<td>{e(assessment.title)}</td>'
        f'<td>{item.score} / 20</td>'
        f'<td>{assessment.coefficient}</td>'
        '</tr>'
        for item, assessment in data["grades"]
    ) or '<tr><td colspan="5">لا توجد نقط معتمدة خلال الفترة.</td></tr>'
    return (
        '<section class="pc-section"><h2>مؤشرات الحضور والغياب</h2>'
        f'<div class="pc-kpis">{cards}</div>'
        '<p class="pc-note">نسبة الحضور مبنية على سجلات الحضور والتأخر '
        'والغياب فقط، ولا تعني أن كل الحصص المبرمجة قد سُجلت.</p>'
        '<h3>تطور المواظبة حسب الشهر</h3>' + month_chart + '</section>'
        '<section class="pc-section"><h2>نتائج التقييمات المعتمدة</h2>'
        '<table><thead><tr><th>المادة</th><th>المعدل الموزون</th>'
        '<th>عدد التقييمات</th></tr></thead><tbody>'
        + subject_rows + '</tbody></table>'
        '<p class="pc-note">المعدلات محسوبة وفق معاملات التقييمات المعتمدة '
        'ضمن الفترة المختارة. التقييمات التي تنتظر المصادقة مستبعدة.</p>'
        '<h3>تطور معدل النقط المعتمدة حسب الشهر</h3>' + grade_bars + '</section>'
        '<section class="pc-section"><h2>تفاصيل الحضور والغياب</h2>'
        '<table><thead><tr><th>التاريخ</th><th>المادة</th>'
        '<th>الحالة</th><th>الإذن</th></tr></thead><tbody>'
        + attendance_rows + '</tbody></table></section>'
        '<section class="pc-section"><h2>تفاصيل التقييمات المعتمدة</h2>'
        '<table><thead><tr><th>التاريخ</th><th>المادة</th>'
        '<th>التقييم</th><th>النقطة</th><th>المعامل</th></tr></thead><tbody>'
        + grades_rows + '</tbody></table></section>'
    )


STYLE = """
<style>
@page{size:A4 portrait;margin:13mm}
.pc-card{font-family:Tahoma,Arial,sans-serif;direction:rtl;color:#16394c;line-height:1.7}
.pc-card h1{font-size:22px;margin:8px 0}
.pc-card h2{font-size:17px;margin:8px 0 13px;color:#136c74}
.pc-card h3{font-size:13px;margin:15px 0 9px;color:#28586c}
.pc-head{display:flex;align-items:center;gap:15px;border-bottom:3px solid #148477;padding-bottom:12px}
.pc-head img{width:62px;height:62px;object-fit:contain}
.pc-meta{padding:12px 15px;background:#ecf6f6;border:1px solid #d1e2e5;margin:15px 0;font-size:13px}
.pc-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}
.pc-kpi{background:#f0f8f8;border:1px solid #d4e5e7;border-radius:8px;padding:9px}
.pc-kpi span{display:block;font-size:11px;color:#4b6c79}
.pc-kpi strong{font-size:19px}
.pc-note,.pc-empty{font-size:11px;color:#526c78;margin:10px 0}
.pc-section{margin:18px 0}
.pc-month-row{display:grid;grid-template-columns:82px 1fr 118px;align-items:center;gap:9px;font-size:11px;margin:9px 0;break-inside:avoid}
.pc-progress{height:17px;background:#e2edf0;border-radius:5px;overflow:hidden}
.pc-progress-fill{height:100%;background:#178c78}
.pc-progress-grade{height:100%;background:#256da4}
.pc-card table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px}
.pc-card td,.pc-card th{border:1px solid #c8dade;padding:7px 8px;text-align:right;overflow-wrap:anywhere}
.pc-card th{background:#e8f3f5;color:#154f63}
.pc-card thead{display:table-header-group}
.pc-card tr{break-inside:avoid;page-break-inside:avoid}
.pc-footer{font-size:11px;color:#547482;border-top:1px solid #bed4dc;margin-top:18px;padding-top:12px}
.pc-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:18px}
.pc-actions a,.pc-actions button{display:inline-block;border:0;border-radius:8px;padding:8px 14px;
background:#157e77;color:white;text-decoration:none;cursor:pointer;font-size:13px}
.pc-paper{background:white;max-width:210mm;margin:16px auto;padding:14mm 12mm;box-shadow:0 3px 23px #1233451c}
.pc-print-body{background:#e8eff2;margin:0}
.pc-print-actions{max-width:210mm;margin:15px auto;padding:8px 15px}
@media print{.pc-print-body{background:white}.pc-actions{display:none!important}
.pc-paper{padding:0;box-shadow:none;margin:0;max-width:none}
.pc-card th,.pc-kpi,.pc-progress-fill,.pc-progress-grade{
-webkit-print-color-adjust:exact;print-color-adjust:exact}}
</style>
"""


def identity_header(learner, center, track, year, start, end):
    return (
        '<div class="pc-head">'
        '<img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل">'
        '<div><strong>جمعية نور الأمل — برنامج الفرصة الثانية</strong>'
        '<h1>بطاقة التتبع الفردي للمستفيد</h1></div></div>'
        '<div class="pc-meta">'
        f'<strong>اسم المستفيد:</strong> {e(learner.full_name)} · '
        f'<strong>المركز:</strong> {e(center.name)} · '
        f'<strong>المسلك:</strong> {e(track.name)} · '
        f'<strong>السنة التكوينية:</strong> {e(year.label)} · '
        f'<strong>الفترة:</strong> {e(start.isoformat())} إلى {e(end.isoformat())}'
        '</div>'
    )


def card_context(db, user, enrollment_id, start, end):
    enrollment = learner_scope(db, user, enrollment_id)
    year, start, end = validate_range(db, enrollment, start, end)
    learner = db.get(Learner, enrollment.learner_id)
    center = db.get(Center, enrollment.center_id)
    track = db.get(Track, enrollment.track_id)
    data = progress_data(db, enrollment, start, end)
    return enrollment, learner, center, track, year, start, end, data


@app.get("/platform/learners/{enrollment_id:int}/progress", response_class=HTMLResponse)
def individual_progress(request: Request, enrollment_id: int,
                        start: date | None = None, end: date | None = None,
                        db: Session = Depends(get_db), user=Depends(actor)):
    enrollment, learner, center, track, year, start, end, data = card_context(
        db, user, enrollment_id, start, end
    )
    query = urlencode({"start": start.isoformat(), "end": end.isoformat()})
    content = (
        STYLE
        + '<section class="pc-card">'
        + identity_header(learner, center, track, year, start, end)
        + '<div class="pc-actions">'
        f'<a href="/platform/learners/{enrollment.id}">العودة لملف المستفيد</a>'
        f'<a href="/platform/learners/{enrollment.id}/progress/print?{query}">'
        'طباعة بطاقة التتبع / حفظ PDF</a></div>'
        f'<form method="get" action="/platform/learners/{enrollment.id}/progress" '
        'class="form-grid no-print">'
        f'<label>من<input type="date" name="start" value="{start.isoformat()}" required></label>'
        f'<label>إلى<input type="date" name="end" value="{end.isoformat()}" required></label>'
        '<button type="submit">عرض الفترة</button></form>'
        + progress_sections(data)
        + '<p class="pc-footer">هذه بطاقة متابعة مبنية على السجلات الموجودة فقط؛ '
        'لا تُعتبر تشخيصاً تربوياً أو قراراً إدارياً.</p>'
        '</section>'
    )
    return page(request, user, "بطاقة التتبع الفردي", content)


@app.get("/platform/learners/{enrollment_id:int}/progress/print",
         response_class=HTMLResponse)
def individual_progress_print(enrollment_id: int,
                              start: date | None = None,
                              end: date | None = None,
                              db: Session = Depends(get_db), user=Depends(actor)):
    enrollment, learner, center, track, year, start, end, data = card_context(
        db, user, enrollment_id, start, end
    )
    query = urlencode({"start": start.isoformat(), "end": end.isoformat()})
    content = (
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>بطاقة التتبع الفردي — ' + escape(learner.full_name, quote=True)
        + '</title>' + STYLE + '</head><body class="pc-print-body">'
        '<div class="pc-actions pc-print-actions">'
        f'<a href="/platform/learners/{enrollment.id}/progress?{query}">العودة للتتبع</a>'
        '<button type="button" onclick="window.print()">طباعة / حفظ PDF</button>'
        '</div><main class="pc-card pc-paper">'
        + identity_header(learner, center, track, year, start, end)
        + progress_sections(data)
        + '<p class="pc-footer">وثيقة داخلية تضم بيانات تربوية شخصية. '
        'تُستعمل للتتبع من طرف الأشخاص المخوّلين فقط.</p>'
        '</main></body></html>'
    )
    return HTMLResponse(content, headers={"Cache-Control": "private, no-store"})
