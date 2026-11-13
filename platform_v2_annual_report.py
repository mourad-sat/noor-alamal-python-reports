"""Private comprehensive annual report from the existing approved evidence.

Read-only: no migration, no modification to attendance, grades, learners,
skills, or integration records. Center and year scope is enforced in SQL.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, center_allowed, e, page, require_role
from platform_v2_integration import OUTCOMES, POSITIVE
from platform_v2_models import (
    Assessment, Attachment, Attendance, Center, Enrollment, Grade,
    IntegrationFollowup, SessionDay, SessionReport, SkillEvaluation,
    Slot, Track, Year,
)

MAX_ACTIVITIES = 60
MAX_PHOTOS = 12

STYLE = """
<style>
@page{size:A4 portrait;margin:13mm}
.annual-root{direction:rtl;color:#193e51;font:13px/1.75 Tahoma,Arial,sans-serif}
.annual-head{display:flex;align-items:center;gap:14px;padding:10px 0;
border-bottom:3px solid #178375}
.annual-head img{width:72px;height:72px;object-fit:contain}
.annual-head strong{font-size:19px}
.annual-head h1{font-size:24px;margin:5px 0;color:#17576a}
.annual-info{border:1px solid #d0e3e5;background:#f0f7f7;padding:12px;margin:16px 0}
.annual-section{margin:21px 0}
.annual-section h2{font-size:18px;color:#1d6070;border-bottom:1px solid #bbd9db;padding-bottom:7px}
.annual-section h3{font-size:14px;color:#2e6072;margin:13px 0 8px}
.annual-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}
.annual-kpi{border:1px solid #c7e0df;border-radius:7px;background:#f2f9f8;padding:10px;break-inside:avoid}
.annual-kpi span{display:block;color:#4d6d7a;font-size:11px}
.annual-kpi strong{display:block;font-size:22px;color:#136e78}
.annual-table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px}
.annual-table th,.annual-table td{border:1px solid #c5dadf;padding:7px;text-align:right;vertical-align:top;overflow-wrap:anywhere}
.annual-table th{background:#eaf4f5}
.annual-table thead{display:table-header-group}
.annual-table tr{break-inside:avoid;page-break-inside:avoid}
.annual-bar-row{display:grid;grid-template-columns:140px 1fr 55px;gap:9px;
align-items:center;margin:9px 0;font-size:11px;break-inside:avoid}
.annual-track{height:14px;background:#e5eff1;border-radius:6px;overflow:hidden}
.annual-fill{height:100%;background:#1b8d80}
.annual-subtle{color:#587280;font-size:11px}
.annual-gallery{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}
.annual-gallery figure{margin:0;border:1px solid #d3e2e4;padding:6px;break-inside:avoid}
.annual-gallery img{width:100%;height:170px;object-fit:contain}
.annual-gallery figcaption{font-size:10px;color:#516a77;overflow-wrap:anywhere}
.annual-actions{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}
.annual-actions button,.annual-actions a{background:#197d76;color:white;
border:0;padding:9px 14px;border-radius:8px;text-decoration:none;cursor:pointer}
.annual-paper{background:white;max-width:210mm;margin:15px auto;padding:13mm 11mm;box-shadow:0 4px 23px #12384d25}
.annual-print-bg{background:#e9f1f3;margin:0}
.annual-footer{border-top:1px solid #c5dce0;margin-top:22px;padding-top:10px;font-size:11px;color:#526e7b}
@media print{
 body.annual-print-bg{background:white}
 .annual-actions,.annual-no-print{display:none!important}
 .annual-paper{max-width:none;box-shadow:none;margin:0;padding:0}
 .annual-kpi,.annual-table th,.annual-fill{
 -webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
"""


def annual_context(db: Session, user, year_id: int | None, center_id: int | None):
    require_role(user, "admin", "coordinator")
    center_id = center_id or None
    years = db.scalars(select(Year).order_by(Year.starts_on.desc())).all()
    if not years:
        return None, years, [], []
    year = next((y for y in years if y.id == year_id), None) if year_id else (
        next((y for y in years if y.active), None) or years[0]
    )
    if year is None:
        raise HTTPException(404, "السنة التكوينية غير موجودة")
    centers = [
        c for c in db.scalars(select(Center).order_by(Center.id)).all()
        if center_allowed(user, c.id)
    ]
    if center_id is not None:
        if not any(c.id == center_id for c in centers):
            raise HTTPException(403, "المركز خارج نطاق صلاحياتك")
        centers = [c for c in centers if c.id == center_id]
    tracks = db.scalars(select(Track).where(
        Track.year_id == year.id,
        Track.center_id.in_([c.id for c in centers]),
    ).order_by(Track.center_id, Track.name)).all() if centers else []
    return year, years, centers, tracks


def annual_data(db: Session, year, centers, tracks):
    center_ids = [c.id for c in centers]
    track_ids = [t.id for t in tracks]
    enrollments = db.scalars(select(Enrollment).where(
        Enrollment.year_id == year.id,
        Enrollment.center_id.in_(center_ids),
        Enrollment.track_id.in_(track_ids),
    )).all() if track_ids else []
    enrollment_ids = {en.id for en in enrollments}
    # Do not count canceled slots or sessions; only approved session reports.
    evidence = db.execute(
        select(SessionDay, SessionReport, Slot, Track)
        .join(SessionReport, SessionReport.session_id == SessionDay.id)
        .join(Slot, Slot.id == SessionDay.slot_id)
        .join(Track, Track.id == Slot.track_id)
        .where(
            SessionReport.status == "approved",
            SessionDay.status != "canceled",
            Slot.canceled.is_(False),
            Slot.year_id == year.id,
            Slot.center_id.in_(center_ids),
            Slot.track_id.in_(track_ids),
            Track.year_id == year.id,
            Track.center_id == Slot.center_id,
            SessionDay.held_on >= year.starts_on,
            SessionDay.held_on <= year.ends_on,
        )
        .order_by(SessionDay.held_on, SessionDay.id)
    ).all() if track_ids else []
    evidence_ids = [session.id for session, _, _, _ in evidence]
    # Only sessions tied to the selected center/year/approved report are
    # eligible. Count attendance for enrollments in that center/year, without
    # scanning every approved report in the whole system.
    # All records are restricted to the selected enrollment and approved sessions.
    attendance = db.scalars(select(Attendance).where(
        Attendance.session_id.in_(evidence_ids),
        Attendance.enrollment_id.in_(enrollment_ids),
    )).all() if evidence_ids and enrollment_ids else []
    sessions_by_id = {s.id: s for s, _, _, _ in evidence}
    recorded_ids = {a.session_id for a in attendance}
    present = sum(a.status in ("present", "late") for a in attendance)
    absent = sum(a.status == "absent" for a in attendance)
    stats = {
        "sessions": len(evidence),
        "recorded": len(recorded_ids),
        "present": present,
        "absent": absent,
        "absent_days": len({
            (a.enrollment_id, sessions_by_id[a.session_id].held_on)
            for a in attendance if a.status == "absent"
        }),
        "missing": len(evidence) - len(recorded_ids),
    }
    assessments = {
        a.id: a for a in db.scalars(select(Assessment).where(
            Assessment.track_id.in_(track_ids),
            Assessment.held_on >= year.starts_on,
            Assessment.held_on <= year.ends_on,
        )).all()
    } if track_ids else {}
    eligible_track = {en.id: en.track_id for en in enrollments}
    approved_grades = db.scalars(select(Grade).where(
        Grade.enrollment_id.in_(enrollment_ids),
        Grade.assessment_id.in_(list(assessments)),
        Grade.status == "approved",
    )).all() if enrollment_ids and assessments else []
    valid_grades = [
        g for g in approved_grades
        if eligible_track[g.enrollment_id] == assessments[g.assessment_id].track_id
        and Decimal(assessments[g.assessment_id].coefficient or 0) > 0
    ]
    denominator = sum((
        Decimal(assessments[g.assessment_id].coefficient)
        for g in valid_grades
    ), Decimal(0))
    average = (
        (sum((
            Decimal(g.score) * Decimal(assessments[g.assessment_id].coefficient)
            for g in valid_grades
        ), Decimal(0)) / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if denominator > 0 else None
    )
    stage_scores = defaultdict(list)
    skill_records = db.scalars(select(SkillEvaluation).where(
        SkillEvaluation.enrollment_id.in_(enrollment_ids),
    )).all() if enrollment_ids else []
    for item in skill_records:
        if item.stage in ("initial", "mid", "final") and 1 <= item.level <= 4:
            stage_scores[item.stage].append(item.level)

    # A learner is counted in exactly one outcome, according to the latest
    # dated followup for that specific annual enrollment.
    history = db.scalars(select(IntegrationFollowup).where(
        IntegrationFollowup.enrollment_id.in_(enrollment_ids)
    ).order_by(
        IntegrationFollowup.observed_on.desc(),
        IntegrationFollowup.id.desc()
    )).all() if enrollment_ids else []
    latest = {}
    for entry in history:
        latest.setdefault(entry.enrollment_id, entry)
    outcomes = Counter(x.outcome for x in latest.values())
    positive = sum(outcomes.get(key, 0) for key in POSITIVE)
    per_center = Counter(en.center_id for en in enrollments)
    per_track = Counter(en.track_id for en in enrollments)
    per_track_sessions = Counter(t.id for _, _, _, t in evidence)

    report_ids = [r.id for _, r, _, _ in evidence]
    photos = db.scalars(select(Attachment).where(
        Attachment.report_id.in_(report_ids),
        Attachment.select_for_summary.is_(True),
        Attachment.content_type.in_(("image/jpeg", "image/png")),
    ).order_by(Attachment.id).limit(MAX_PHOTOS)).all() if report_ids else []
    return {
        "enrollments": enrollments, "stats": stats,
        "grades": len(valid_grades), "average": average,
        "stage_scores": stage_scores, "outcomes": outcomes, "latest": latest,
        "positive": positive, "per_center": per_center,
        "per_track": per_track, "per_track_sessions": per_track_sessions,
        "evidence": evidence, "photos": photos,
    }


def mini_table(headers, rows):
    head = "".join(f'<th>{e(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    ) or f'<tr><td colspan="{len(headers)}">لا تتوفر بيانات لهذه السنة.</td></tr>'
    return f'<table class="annual-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def bar_chart(rows, max_value=None):
    max_value = max_value if max_value is not None else max(
        (n for _, n in rows), default=0
    )
    if not max_value:
        return '<p class="annual-subtle">لا تتوفر بيانات كافية لعرض المبيان.</p>'
    return "".join(
        '<div class="annual-bar-row">'
        f'<span>{e(label)}</span>'
        f'<div class="annual-track" role="img" aria-label="{e(label)}: {count}">'
        f'<div class="annual-fill" style="width:{round(100 * count / max_value, 1)}%"></div>'
        '</div>'
        f'<strong>{count}</strong></div>'
        for label, count in rows
    )


def annual_sections(year, centers, tracks, data, for_print=False):
    st = data["stats"]
    num_learners = len(data["enrollments"])
    records = st["present"] + st["absent"]
    attendance_rate = round(st["present"] * 100 / records, 1) if records else None
    rows = [
        ("المستفيدون المسجلون", num_learners),
        ("الحصص المصادق عليها", st["sessions"]),
        ("حصص بها تسجيل حضور", st["recorded"]),
        ("سجلات الحضور والتأخر", st["present"]),
        ("سجلات الغياب", st["absent"]),
        ("أيام غياب المستفيدين", st["absent_days"]),
        ("التقييمات بالنقط المصادق عليها", data["grades"]),
        ("الملفات ذات متابعة الإدماج", len(data["latest"])),
        ("مسارات الإدماج المسجلة", data["positive"]),
    ]
    cards = "".join(
        f'<div class="annual-kpi"><span>{e(label)}</span><strong>{val}</strong></div>'
        for label, val in rows
    )
    by_center = [
        [e(c.name), str(data["per_center"][c.id]),
         str(sum(data["per_track_sessions"][t.id] for t in tracks if t.center_id == c.id))]
        for c in centers
    ]
    by_track = [
        [e(next(c.name for c in centers if c.id == t.center_id)),
         e(t.name), str(data["per_track"][t.id]),
         str(data["per_track_sessions"][t.id])]
        for t in tracks
    ]
    skills = []
    for stage, label in (
        ("initial", "التقييم الأولي"),
        ("mid", "التقييم المرحلي"),
        ("final", "التقييم النهائي"),
    ):
        values = data["stage_scores"][stage]
        avg = f'{sum(values) / len(values):.2f} / 4' if values else "غير متاح"
        skills.append([e(label), str(len(values)), avg])
    outcomes = [
        (label, data["outcomes"].get(key, 0))
        for key, label in OUTCOMES.items()
    ]
    activities = [
        [
            e(session.held_on.isoformat()),
            e(next(c.name for c in centers if c.id == slot.center_id)),
            e(track.name),
            e(slot.subject),
            e(report.activities.strip()[:450]) if report.activities.strip() else "—",
            f"#{report.id}",
        ]
        for session, report, slot, track in data["evidence"][:MAX_ACTIVITIES]
    ]
    if len(data["evidence"]) > MAX_ACTIVITIES:
        more = (
            f'<p class="annual-subtle">يعرض الجدول {MAX_ACTIVITIES} '
            f'من أصل {len(data["evidence"])} حصة معتمدة. '
            'الأرقام الإجمالية تشمل جميع الحصص.</p>'
        )
    else:
        more = ""
    photos = "".join(
        '<figure>'
        f'<img src="/platform/attachments/{item.id}/view" '
        f'alt="{e(item.original_name)}" loading="eager">'
        f'<figcaption>{e(item.original_name)} · تقرير الحصة #{item.report_id}</figcaption>'
        '</figure>'
        for item in data["photos"]
    )
    if not photos:
        photos = '<p class="annual-subtle">لا توجد صور مختارة من تقارير الحصص المعتمدة.</p>'
    if attendance_rate is not None:
        attendance_label = f"{attendance_rate}% من سجلات الحضور والغياب"
    else:
        attendance_label = "غير متاحة لغياب سجلات حضور أو غياب"
    return (
        '<section class="annual-section"><h2>1. ملخص المؤشرات</h2>'
        '<div class="annual-kpis">' + cards + '</div>'
        '<p class="annual-subtle">نسبة الحضور: ' + attendance_label
        + '. النسبة مبنية على السجلات المدخلة للحصص المعتمدة، '
        'ولا تمثل نسبة حضور كل المستفيدين. '
        f'حصص معتمدة دون تسجيل حضور: {st["missing"]}.</p></section>'
        '<section class="annual-section"><h2>2. الحصيلة حسب المركز</h2>'
        + mini_table(["المركز", "المستفيدون", "الحصص المعتمدة"], by_center)
        + '</section>'
        '<section class="annual-section"><h2>3. الحصيلة حسب المسلك</h2>'
        + mini_table(["المركز", "المسلك", "المستفيدون", "الحصص"], by_track)
        + '<h3>مقارنة الحصص المعتمدة حسب المسلك</h3>'
        + bar_chart([(t.name, data["per_track_sessions"][t.id]) for t in tracks])
        + '</section>'
        '<section class="annual-section"><h2>4. نتائج النقط المعتمدة</h2>'
        f'<p>عدد النقط المصادق عليها ذات معامل صالح: <strong>{data["grades"]}</strong>. '
        f'المعدل الموزون للسجلات المتوفرة: <strong>{data["average"] if data["average"] is not None else "غير متاح"}'
        '</strong> / 20.</p>'
        '<p class="annual-subtle">معدل وصفي للسجلات المعتمدة، وليس معدل نجاح '
        'أو معدل المستفيدين الفريدين. النقط المعلقة مستبعدة.</p></section>'
        '<section class="annual-section"><h2>5. تطور الكفايات</h2>'
        + mini_table(["المرحلة", "التقييمات المسجلة", "متوسط المستوى"], skills)
        + '<p class="annual-subtle">هذه متوسطات للتقييمات المسجلة بكل مرحلة '
        'وليست مقارنة مزدوجة لنفس المستفيدين؛ لا يتم احتساب تقييمات مفقودة كأصفار.</p>'
        '</section>'
        '<section class="annual-section"><h2>6. الإدماج بعد التكوين</h2>'
        + mini_table(
            ["الوضعية حسب أحدث متابعة", "عدد المستفيدين"],
            [[e(label), str(number)] for label, number in outcomes],
        )
        + bar_chart(outcomes)
        + f'<p class="annual-subtle">من أصل {num_learners} مستفيد: '
        f'{len(data["latest"])} لديهم متابعة مسجلة، '
        f'{num_learners-len(data["latest"])} دون متابعة. '
        'وضعيات الإدماج مبنية على آخر متابعة لكل تسجيل، '
        'وتحتاج إلى تحقق قبل استعمالها كحصيلة مؤكدة للشركاء.</p></section>'
        '<section class="annual-section"><h2>7. نماذج الأنشطة المعتمدة</h2>'
        + mini_table(
            ["التاريخ", "المركز", "المسلك", "المادة", "النشاط المسجل", "المرجع"],
            activities,
        ) + more + '</section>'
        '<section class="annual-section"><h2>8. صور توثيقية مختارة</h2>'
        '<div class="annual-gallery">' + photos + '</div>'
        '<p class="annual-subtle">صور داخلية اختارها المسؤول من مرفقات '
        'تقارير حصص مصادق عليها. الاختيار لا يمثل موافقة على نشر صور المستفيدين '
        'خارج إطار الجمعية.</p></section>'
        '<section class="annual-section"><h2>9. خلاصة إدارية</h2>'
        '<p>هذا التقرير السنوي يجمع المؤشرات المتوفرة آلياً من السجلات '
        'الموجودة داخل النطاق المعتمد. يُستحسن أن تراجع الإدارة النتائج '
        'وتضيف الخلاصات والتوصيات النوعية في تقرير إداري معتمد قبل '
        'تقديمه للشركاء.</p></section>'
    )


def report_html(year, centers, tracks, data):
    location = "جميع المراكز المخوّل الاطلاع عليها" if len(centers) != 1 else centers[0].name
    return (
        '<div class="annual-root">'
        '<header class="annual-head">'
        '<img src="/static/noor_official_logo.webp" alt="شعار الجمعية">'
        '<div><strong>جمعية نور الأمل · برنامج الفرصة الثانية – الجيل الجديد</strong>'
        '<h1>التقرير السنوي الشامل</h1></div></header>'
        '<div class="annual-info">'
        f'<strong>السنة التكوينية:</strong> {e(year.label)} · '
        f'<strong>من:</strong> {year.starts_on.isoformat()} · '
        f'<strong>إلى:</strong> {year.ends_on.isoformat()} · '
        f'<strong>المراكز:</strong> {e(location)}</div>'
        '<p class="annual-subtle">وثيقة إحصائية أولية مستخرجة من النظام، '
        'غير مصادق عليها إدارياً. لا تشمل بيانات غير معتمدة حيث يلزم الاعتماد.</p>'
        + annual_sections(year, centers, tracks, data)
        + '<footer class="annual-footer">تقرير داخلي يحتوي بيانات تربوية '
        'مجمعة وصوراً خاصة. الطباعة أو الحفظ للاستخدام المخوّل فقط.</footer></div>'
    )


@app.get("/platform/annual-report", response_class=HTMLResponse)
def annual_report(request: Request, year_id: int | None = None,
                  center_id: int | None = None, db: Session = Depends(get_db),
                  user=Depends(actor)):
    year, years, centers, tracks = annual_context(
        db, user, year_id, center_id
    )
    center_id = center_id or None
    if year is None:
        return page(request, user, "التقرير السنوي الشامل",
                    '<section><p>لا توجد سنة تكوينية مسجلة بعد.</p></section>')
    year_options = "".join(
        f'<option value="{y.id}" {"selected" if y.id == year.id else ""}>'
        f'{e(y.label)}</option>' for y in years
    )
    # All allowed centers, not only the selected one.
    allowed_centers = [c for c in db.scalars(select(Center)).all()
                       if center_allowed(user, c.id)]
    center_options = (
        '<option value="0">جميع المراكز المخوّل بها</option>' + "".join(
            f'<option value="{c.id}" {"selected" if c.id == center_id else ""}>'
            f'{e(c.name)}</option>' for c in allowed_centers
        )
    )
    query = f'year_id={year.id}'
    if center_id is not None:
        query += f'&center_id={center_id}'
    controls = (
        '<section class="annual-no-print">'
        '<h2>إعداد التقرير السنوي الشامل</h2>'
        '<form method="get" action="/platform/annual-report" class="form-grid">'
        f'<label>السنة التكوينية<select name="year_id">{year_options}</select></label>'
        f'<label>المركز<select name="center_id">{center_options}</select></label>'
        '<button type="submit">عرض الحصيلة</button></form>'
        '<div class="annual-actions">'
        f'<a href="/platform/annual-report/print?{query}">طباعة أو حفظ PDF</a>'
        '</div></section>'
    )
    data = annual_data(db, year, centers, tracks)
    return page(request, user, "التقرير السنوي الشامل",
                STYLE + controls + '<section class="annual-root">'
                + report_html(year, centers, tracks, data) + '</section>')


@app.get("/platform/annual-report/print", response_class=HTMLResponse)
def annual_report_print(year_id: int | None = None,
                        center_id: int | None = None,
                        db: Session = Depends(get_db), user=Depends(actor)):
    year, _, centers, tracks = annual_context(db, user, year_id, center_id)
    if year is None:
        raise HTTPException(404, "السنة غير موجودة")
    data = annual_data(db, year, centers, tracks)
    html = (
        '<!doctype html><html dir="rtl" lang="ar"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>التقرير السنوي الشامل — ' + e(year.label) + '</title>'
        + STYLE + '</head><body class="annual-print-bg">'
        '<div class="annual-actions annual-paper">'
        f'<a href="/platform/annual-report?year_id={year.id}" class="annual-no-print">'
        'العودة للتقرير</a>'
        '<button type="button" onclick="window.print()">طباعة / حفظ PDF</button></div>'
        '<main class="annual-paper">' + report_html(year, centers, tracks, data)
        + '</main></body></html>'
    )
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})
