"""Confidential, append-only follow-up of each learner's pathway after training."""
from __future__ import annotations

from collections import Counter
from datetime import date
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (
    actor, assert_csrf, center_allowed, coordinator, e, form, learner_scope,
    log, page, redirect, require_role, table, visible_tracks,
)
from platform_v2_models import (
    Center, Enrollment, IntegrationFollowup, Learner, Track, Year,
)

OUTCOMES = {
    "continued_study": "متابعة الدراسة",
    "vocational_training": "الالتحاق بتكوين مهني",
    "employed": "الاندماج في سوق الشغل",
    "seeking": "البحث عن فرصة",
    "needs_support": "في حاجة إلى المواكبة",
    "unknown": "الوضعية غير مؤكدة",
}
POSITIVE = {"continued_study", "vocational_training", "employed"}


def fetch_pathway(db: Session, enrollment_id: int):
    return db.scalars(
        select(IntegrationFollowup)
        .where(IntegrationFollowup.enrollment_id == enrollment_id)
        .order_by(
            IntegrationFollowup.observed_on.desc(),
            IntegrationFollowup.id.desc(),
        )
    ).all()


def scope(db: Session, user, enrollment_id: int):
    en = learner_scope(db, user, enrollment_id)
    return (en, db.get(Learner, en.learner_id),
            db.get(Center, en.center_id), db.get(Track, en.track_id),
            db.get(Year, en.year_id))


def report_header(en, learner, center, track, year):
    return (
        '<header class="if-head"><img src="/static/noor_official_logo.webp" '
        'alt="شعار جمعية نور الأمل"><div><strong>جمعية نور الأمل</strong>'
        '<h1>بطاقة تتبع الإدماج بعد التكوين</h1></div></header>'
        '<p class="if-meta">'
        f'<strong>المستفيد:</strong> {e(learner.full_name)} · '
        f'<strong>المركز:</strong> {e(center.name)} · '
        f'<strong>المسلك:</strong> {e(track.name)} · '
        f'<strong>السنة التكوينية:</strong> {e(year.label)}</p>'
    )


def history_html(items):
    rows = []
    for item in items:
        rows.append([
            e(item.observed_on.isoformat()),
            e(OUTCOMES.get(item.outcome, "غير محدد")),
            e(item.organization) if item.organization else "—",
            e(item.pathway_details) if item.pathway_details else "—",
            e(item.intervention) if item.intervention else "—",
            e(item.next_followup_on.isoformat()) if item.next_followup_on else "—",
            e(item.recorded_by),
        ])
    return table(
        ["تاريخ المعاينة", "الوضعية", "المؤسسة أو الوجهة", "التفاصيل",
         "التدخل أو المواكبة", "المتابعة المقبلة", "سجّلها"], rows
    )


STYLE = """
<style>
@page{size:A4 landscape;margin:12mm}
.if-sheet{font-family:Tahoma,Arial,sans-serif;direction:rtl;line-height:1.6;color:#193d50}
.if-head{display:flex;align-items:center;gap:13px;border-bottom:3px solid #168779;padding-bottom:12px}
.if-head img{width:62px;height:62px;object-fit:contain}
.if-head h1{font-size:20px;margin:4px 0;color:#145b6c}
.if-meta{padding:9px 12px;background:#edf6f6;border:1px solid #d2e1e4;font-size:12px}
.if-sheet h2{font-size:17px;color:#176071}
.if-status{font-weight:bold;font-size:14px;padding:10px 13px;border-right:4px solid #168779;background:#f0f7f6}
.if-note{font-size:12px;color:#4e6a78}
.if-sheet .table-scroll{overflow-x:auto}
.if-sheet table{width:100%;border-collapse:collapse;min-width:850px;font-size:11px}
.if-sheet th,.if-sheet td{border:1px solid #c3d6dd;padding:7px;text-align:right;vertical-align:top;overflow-wrap:anywhere}
.if-sheet th{background:#e9f2f3}
.if-sheet tr{break-inside:avoid;page-break-inside:avoid}
.if-sheet thead{display:table-header-group}
.if-actions{display:flex;flex-wrap:wrap;gap:10px;margin:15px 0}
.if-actions a,.if-actions button{background:#167d74;color:white;text-decoration:none;padding:9px 14px;border:0;border-radius:7px;cursor:pointer}
.if-paper{background:white;padding:13mm;max-width:297mm;margin:15px auto;box-shadow:0 3px 25px #173b4c25}
.if-bg{margin:0;background:#e8eff2}
.if-dashboard-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.if-kpi{padding:12px;background:#f0f7f7;border:1px solid #cfe0e3;border-radius:9px}
.if-kpi strong{display:block;font-size:24px}
.if-bars{display:grid;gap:8px;margin:10px 0 20px}
.if-bar{display:grid;grid-template-columns:160px 1fr 42px;gap:8px;align-items:center}
.if-bar-track{background:#e1ecee;border-radius:6px;height:13px;overflow:hidden}
.if-bar-fill{background:#198b7e;height:100%}
.if-muted{font-size:12px;color:#526e7a}
@media print{
 .if-bg{background:white}.if-actions,.no-print{display:none!important}
 .if-paper{padding:0;margin:0;box-shadow:none;max-width:none}
 .if-sheet .table-scroll{overflow:visible}
 .if-sheet table{min-width:0}
 .if-sheet th,.if-bar-fill{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
"""


@app.get("/platform/learners/{enrollment_id:int}/integration", response_class=HTMLResponse)
def individual_integration(request: Request, enrollment_id: int,
                           db: Session = Depends(get_db), user=Depends(actor)):
    en, learner, center, track, year = scope(db, user, enrollment_id)
    items = fetch_pathway(db, en.id)
    status = (
        f'{e(OUTCOMES.get(items[0].outcome, "غير مؤكدة"))} · '
        f'آخر متابعة {e(items[0].observed_on.isoformat())}'
        if items else "لم تُسجّل معطيات إدماج بعد"
    )
    form_html = ""
    if user["role"] in ("admin", "coordinator"):
        opts = "".join(
            f'<option value="{e(key)}">{e(label)}</option>'
            for key, label in OUTCOMES.items()
        )
        form_html = (
            '<section><h2>تسجيل متابعة جديدة</h2>'
            '<p class="if-note">كل متابعة تُحفظ كسجل مستقل حتى يبقى تاريخ '
            'تطور مسار المستفيد موثّقاً. لا يتم تعديل التسجيل الدراسي أو النقط.</p>'
            + form(
                f"/platform/learners/{en.id}/integration", request.session["csrf"],
                '<label>وضعية المستفيد<select name="outcome" required>'
                + opts + '</select></label>'
                '<label>تاريخ المعاينة<input type="date" name="observed_on" '
                f'max="{date.today().isoformat()}" value="{date.today().isoformat()}" required></label>'
                '<label>المؤسسة أو جهة الإدماج (اختياري)'
                '<input name="organization" maxlength="180" autocomplete="off"></label>'
                '<label>تفاصيل المسار (اختياري)'
                '<textarea name="pathway_details" maxlength="300" rows="2"></textarea></label>'
                '<label>الإجراء أو المواكبة المنجزة (اختياري)'
                '<textarea name="intervention" maxlength="500" rows="3"></textarea></label>'
                '<label>تاريخ المتابعة المقبلة (اختياري)'
                '<input type="date" name="next_followup_on"></label>',
                "حفظ متابعة جديدة"
            ) + '</section>'
        )
    body = (
        STYLE + '<section class="if-sheet">'
        + report_header(en, learner, center, track, year)
        + '<div class="if-actions">'
        f'<a href="/platform/learners/{en.id}">العودة لملف المستفيد</a>'
        f'<a href="/platform/learners/{en.id}/integration/print">طباعة بطاقة الإدماج / حفظ PDF</a>'
        '</div>'
        f'<p class="if-status">{status}</p>'
        '<p class="if-note">الوضعية مبنية على أحدث متابعة مؤرخة ومسجلة، '
        'وليست دليلاً تلقائياً على إدماج مؤكد بدون تحقق بشري.</p>'
        '<h2>تاريخ المتابعة والإجراءات</h2>' + history_html(items)
        + form_html + '</section>'
    )
    return page(request, user, "تتبع الإدماج بعد التكوين", body)


@app.post("/platform/learners/{enrollment_id:int}/integration")
def add_integration(request: Request, enrollment_id: int,
                    token: str = Form(alias="_csrf"),
                    outcome: str = Form(), observed_on: date = Form(),
                    organization: str = Form(""),
                    pathway_details: str = Form(""),
                    intervention: str = Form(""),
                    next_followup_on: date | None = Form(None),
                    db: Session = Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    require_role(user, "admin", "coordinator")
    en, _, _, _, _ = scope(db, user, enrollment_id)
    coordinator(user, en.center_id)
    if (
        outcome not in OUTCOMES or observed_on > date.today()
        or (next_followup_on is not None and next_followup_on < observed_on)
        or len(organization.strip()) > 180
        or len(pathway_details.strip()) > 300
        or len(intervention.strip()) > 500
    ):
        raise HTTPException(422, "معطيات المتابعة أو التواريخ غير صحيحة")
    item = IntegrationFollowup(
        enrollment_id=en.id,
        outcome=outcome,
        observed_on=observed_on,
        organization=organization.strip(),
        pathway_details=pathway_details.strip(),
        intervention=intervention.strip(),
        next_followup_on=next_followup_on,
        author_id=user["id"],
        recorded_by=user["name"],
    )
    db.add(item)
    db.flush()
    log(db, user, "create", "integration_followup", item.id)
    db.commit()
    return redirect(f"/platform/learners/{en.id}/integration")


@app.get("/platform/learners/{enrollment_id:int}/integration/print",
         response_class=HTMLResponse)
def print_integration(enrollment_id: int, db: Session = Depends(get_db),
                      user=Depends(actor)):
    en, learner, center, track, year = scope(db, user, enrollment_id)
    items = fetch_pathway(db, en.id)
    content = (
        '<!doctype html><html lang="ar" dir="rtl"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>بطاقة تتبع الإدماج — {e(learner.full_name)}</title>'
        + STYLE + '</head><body class="if-bg">'
        '<nav class="if-actions if-paper">'
        f'<a href="/platform/learners/{en.id}/integration">العودة للمتابعة</a>'
        '<button type="button" onclick="window.print()">طباعة / حفظ PDF</button></nav>'
        '<main class="if-sheet if-paper">'
        + report_header(en, learner, center, track, year)
        + '<h2>تاريخ الوضعيات والتدخلات</h2>' + history_html(items)
        + '<p class="if-note">وثيقة متابعة داخلية تحتوي على معطيات شخصية، '
        'ومخصصة للمستخدمين المخولين فقط.</p>'
        '</main></body></html>'
    )
    return HTMLResponse(content, headers={"Cache-Control": "private, no-store"})


@app.get("/platform/integration", response_class=HTMLResponse)
def integration_overview(request: Request, track_id: int | None = None,
                         db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    tracks = [t for t in visible_tracks(db, user)
              if t.center_id and user["role"] in ("admin", "coordinator")]
    selected = next((t for t in tracks if t.id == track_id), None)
    if track_id is not None and selected is None:
        raise HTTPException(403, "المسلك غير متاح")
    if selected is None:
        opts = "".join(
            '<a class="button" '
            f'href="/platform/integration?track_id={t.id}">'
            f'{e(db.get(Center, t.center_id).name)} · {e(t.name)} · '
            f'{e(db.get(Year, t.year_id).label)}</a> '
            for t in tracks
        )
        return page(
            request, user, "تتبع الإدماج بعد التكوين",
            STYLE + '<section><h2>اختر المركز والمسلك</h2>'
            '<p>يُعرض ملخص الإدماج للمسلك المختار فقط، '
            'ولا تُحسب الملفات دون متابعة على أنها مدمجة.</p>'
            + (opts or '<p>لا توجد مسالك متاحة.</p>') + '</section>',
        )

    enrollments = db.scalars(
        select(Enrollment).where(
            Enrollment.center_id == selected.center_id,
            Enrollment.track_id == selected.id,
            Enrollment.year_id == selected.year_id,
        ).order_by(Enrollment.id)
    ).all()
    ids = [en.id for en in enrollments]
    learners = {l.id: l for l in db.scalars(
        select(Learner).where(Learner.id.in_(
            [en.learner_id for en in enrollments]
        ))
    ).all()} if enrollments else {}
    records = db.scalars(
        select(IntegrationFollowup)
        .where(IntegrationFollowup.enrollment_id.in_(ids))
        .order_by(IntegrationFollowup.observed_on.desc(),
                  IntegrationFollowup.id.desc())
    ).all() if ids else []
    latest = {}
    for row in records:
        latest.setdefault(row.enrollment_id, row)
    counts = Counter(latest[en.id].outcome for en in enrollments
                     if en.id in latest)
    known = len(latest)
    positive = sum(counts.get(value, 0) for value in POSITIVE)
    rows = []
    for en in enrollments:
        item = latest.get(en.id)
        rows.append([
            f'<a href="/platform/learners/{en.id}/integration">'
            f'{e(learners[en.learner_id].full_name)}</a>',
            e(OUTCOMES[item.outcome]) if item else "لم تُسجّل متابعة",
            e(item.observed_on.isoformat()) if item else "—",
            e(item.next_followup_on.isoformat())
                if item and item.next_followup_on else "—",
        ])
    bars = []
    for key, label in OUTCOMES.items():
        count = counts.get(key, 0)
        percent = round(count * 100 / known, 1) if known else 0
        bars.append(
            '<div class="if-bar"><span>' + e(label) + '</span>'
            f'<div class="if-bar-track" role="img" aria-label="{e(label)}: {count}">'
            f'<div class="if-bar-fill" style="width:{percent}%"></div></div>'
            f'<strong>{count}</strong></div>'
        )
    body = (
        STYLE + '<section class="if-sheet">'
        f'<h2>حصيلة الإدماج: {e(db.get(Center, selected.center_id).name)}'
        f' · {e(selected.name)}</h2>'
        '<p class="if-note">تُحسب أحدث وضعية لكل مستفيد مرة واحدة، '
        'ولا تُعتبر الحالات غير الموثقة إدماجاً محققاً.</p>'
        '<div class="if-dashboard-grid">'
        f'<div class="if-kpi">عدد المستفيدين<strong>{len(enrollments)}</strong></div>'
        f'<div class="if-kpi">ملفات تمت متابعتها<strong>{known}</strong></div>'
        f'<div class="if-kpi">المتابعات ذات مسار إدماج مسجل<strong>{positive}</strong></div>'
        '</div>'
        '<h2>توزيع الوضعيات المسجلة</h2>'
        '<div class="if-bars">' + "".join(bars) + '</div>'
        '<h2>لائحة المستفيدين حسب آخر متابعة</h2>'
        + table(["المستفيد", "آخر وضعية", "تاريخ المعاينة", "المتابعة المقبلة"], rows)
        + '<p class="if-note">التصنيف إداري بناءً على المعطيات المدخلة؛ '
        'يتطلب التأكد من الوضعيات قبل استعمالها في تقارير الشركاء.</p>'
        '</section>'
    )
    return page(request, user, "حصيلة الإدماج حسب المسلك", body)
