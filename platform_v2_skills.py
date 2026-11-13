"""Private competency rubric for each learner, independent of official grades."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import actor, assert_csrf, e, learner_scope, log, page, redirect
from platform_v2_models import (
    Center, Enrollment, Learner, SkillEvaluation, Track, Year,
)

SKILLS = {
    "communication": "التواصل والتعبير",
    "teamwork": "العمل الجماعي",
    "digital": "المهارات الرقمية",
    "literacy": "القراءة والكتابة",
    "numeracy": "الحساب والتفكير المنطقي",
    "autonomy": "الاستقلالية وتحمل المسؤولية",
    "professional": "المهارات المهنية",
}
STAGES = {
    "initial": "التقييم الأولي",
    "mid": "التقييم المرحلي",
    "final": "التقييم النهائي",
}
LEVELS = {
    1: "1 — يحتاج إلى دعم",
    2: "2 — في طور الاكتساب",
    3: "3 — مكتسب",
    4: "4 — متقن",
}

PRINT_STYLE = """
<style>
@page{size:A4 portrait;margin:13mm}
.skill-sheet{font-family:Tahoma,Arial,sans-serif;color:#204055;direction:rtl;line-height:1.65}
.skill-head{display:flex;align-items:center;gap:15px;border-bottom:3px solid #188477;padding-bottom:12px}
.skill-head img{height:60px;width:60px;object-fit:contain}
.skill-head strong{font-size:17px}
.skill-head h1{font-size:20px;margin:3px 0;color:#15586a}
.skill-meta{background:#edf6f6;border:1px solid #cadfe0;padding:9px 12px;margin:14px 0;font-size:12px}
.skill-sheet h2{font-size:17px;color:#155d72;margin:18px 0 10px}
.skill-sheet h3{font-size:13px;color:#28576e;margin:13px 0 7px}
.skill-note{color:#58717d;font-size:11px}
.skill-sheet table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:11px}
.skill-sheet th,.skill-sheet td{border:1px solid #c4d9de;padding:8px;text-align:right;overflow-wrap:anywhere}
.skill-sheet th{background:#e8f2f3}
.skill-sheet thead{display:table-header-group}
.skill-sheet tr{break-inside:avoid;page-break-inside:avoid}
.skill-bar-row{display:grid;grid-template-columns:minmax(125px,31%) repeat(3,minmax(0,1fr));
gap:7px;align-items:center;font-size:11px;padding:7px 0;border-bottom:1px solid #e0e9ec;break-inside:avoid}
.skill-bar-cell{min-width:0}
.skill-bar-track{height:10px;background:#e4eff1;border-radius:5px;overflow:hidden}
.skill-bar-fill{height:100%;background:#188b7d}
.skill-bar-cell:nth-child(3) .skill-bar-fill{background:#3284b1}
.skill-bar-cell:nth-child(4) .skill-bar-fill{background:#6d69bb}
.skill-bar-cell small{font-size:10px;color:#486b78}
.skill-actions{margin:15px 0;display:flex;gap:8px;flex-wrap:wrap}
.skill-actions a,.skill-actions button{background:#127b77;color:white;padding:9px 13px;border:0;border-radius:7px;text-decoration:none;cursor:pointer}
.skill-paper{max-width:210mm;margin:16px auto;background:white;padding:13mm 12mm;box-shadow:0 4px 24px #18384c27}
.skill-print-bg{background:#e8eef1;margin:0}
.skill-print-toolbar{max-width:210mm;margin:10px auto}
.skill-comment{font-size:11px;padding:6px 0;border-bottom:1px solid #e7eded;overflow-wrap:anywhere}
@media(max-width:600px){
.skill-head h1{font-size:17px}
.skill-head img{height:48px;width:48px}
.skill-bar-row{grid-template-columns:minmax(95px,30%) repeat(3,minmax(0,1fr));gap:4px}
.skill-bar-row strong{font-size:10px;overflow-wrap:anywhere}
.skill-sheet table{font-size:10px}
.skill-sheet td,.skill-sheet th{padding:5px}
}
@media print{
 .skill-print-bg{background:white}
 .skill-actions{display:none!important}
 .skill-paper{max-width:none;margin:0;padding:0;box-shadow:none}
 .skill-bar-fill,.skill-sheet th{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
"""


def scoped(db: Session, user, enrollment_id: int):
    en = learner_scope(db, user, enrollment_id)
    learner = db.get(Learner, en.learner_id)
    center = db.get(Center, en.center_id)
    track = db.get(Track, en.track_id)
    year = db.get(Year, en.year_id)
    return en, learner, center, track, year


def skill_records(db: Session, enrollment_id: int):
    return db.scalars(
        select(SkillEvaluation).where(
            SkillEvaluation.enrollment_id == enrollment_id
        ).order_by(SkillEvaluation.evaluated_on, SkillEvaluation.id)
    ).all()


def identity(learner, center, track, year):
    return (
        '<header class="skill-head">'
        '<img src="/static/noor_official_logo.webp" alt="شعار الجمعية">'
        '<div><strong>جمعية نور الأمل · برنامج الفرصة الثانية</strong>'
        '<h1>شبكة تقييم المهارات والكفايات</h1></div></header>'
        '<div class="skill-meta">'
        f'<strong>المستفيد:</strong> {e(learner.full_name)} · '
        f'<strong>المركز:</strong> {e(center.name)} · '
        f'<strong>المسلك:</strong> {e(track.name)} · '
        f'<strong>السنة:</strong> {e(year.label)}</div>'
    )


def skill_sections(records: list[SkillEvaluation]) -> str:
    by_pair = {(r.skill_key, r.stage): r for r in records}
    th = "".join(f'<th>{e(name)}</th>' for name in STAGES.values())
    rows = []
    bars = []
    for key, name in SKILLS.items():
        cells = []
        bar_cells = []
        for stage in STAGES:
            item = by_pair.get((key, stage))
            if item:
                cells.append(
                    f'<td><strong>{item.level}/4</strong> '
                    f'<small>({e(item.evaluated_on.isoformat())})</small></td>'
                )
                bar_cells.append(
                    '<div class="skill-bar-cell">'
                    f'<div class="skill-bar-track" role="img" aria-label="'
                    f'{e(name)} {e(STAGES[stage])}: {item.level} من 4">'
                    f'<div class="skill-bar-fill" style="width:{item.level * 25}%"></div></div>'
                    f'<small>{item.level}/4</small></div>'
                )
            else:
                cells.append('<td>—</td>')
                bar_cells.append('<div class="skill-bar-cell"><small>لم يُقيّم</small></div>')
        rows.append(f'<tr><th scope="row">{e(name)}</th>{"".join(cells)}</tr>')
        bars.append(
            f'<div class="skill-bar-row"><strong>{e(name)}</strong>'
            + "".join(bar_cells) + '</div>'
        )

    note_rows = "".join(
        '<div class="skill-comment">'
        f'<strong>{e(SKILLS[item.skill_key])} · {e(STAGES[item.stage])} '
        f'({e(item.evaluated_on.isoformat())}):</strong> {e(item.comment)}</div>'
        for item in records if item.comment.strip()
    ) or '<p class="skill-note">لا توجد ملاحظات تربوية مسجلة بعد.</p>'

    stage_summary = []
    for key, stage_name in STAGES.items():
        entries = [r.level for r in records if r.stage == key]
        if entries:
            avg = sum(entries) / len(entries)
            stage_summary.append(
                f'{e(stage_name)}: {avg:.2f}/4 '
                f'(عدد المهارات المقيمة: {len(entries)})'
            )
        else:
            stage_summary.append(f'{e(stage_name)}: لا توجد تقييمات')
    return (
        '<section><h2>جدول مقارنة المهارات حسب مراحل التكوين</h2>'
        '<p class="skill-note">السلم: 1 يحتاج إلى دعم، 2 في طور الاكتساب، '
        '3 مكتسب، 4 متقن. علامة — تعني غياب التقييم، وليست صفراً.</p>'
        '<table><thead><tr><th>المهارة</th>' + th + '</tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></section>'
        '<section><h2>مبيان تطور مستوى المهارات</h2>'
        '<div class="skill-bar-row"><strong>المهارة</strong>'
        + "".join(f'<strong>{e(x)}</strong>' for x in STAGES.values())
        + '</div>' + "".join(bars)
        + '<p class="skill-note">' + ' · '.join(stage_summary) + '</p></section>'
        '<section><h2>الملاحظات التربوية</h2>' + note_rows + '</section>'
    )


@app.get("/platform/learners/{enrollment_id:int}/skills", response_class=HTMLResponse)
def skill_grid(request: Request, enrollment_id: int,
               db: Session = Depends(get_db), user=Depends(actor)):
    en, learner, center, track, year = scoped(db, user, enrollment_id)
    records = skill_records(db, en.id)
    options = lambda mapping: "".join(
        f'<option value="{e(key)}">{e(value)}</option>'
        for key, value in mapping.items()
    )
    csrf = request.session["csrf"]
    editable = (
        '<section><h2>تسجيل تقييم مهارة</h2>'
        '<p class="skill-note">قيّم كل مهارة في مرحلة البداية أو الوسط أو النهاية. '
        'يُسجل لكل مهارة تقييم واحد في كل مرحلة. '
        'تصحيح تقييم مسجل يتطلب طلباً من المنسق أو الإدارة.</p>'
        f'<form method="post" action="/platform/learners/{en.id}/skills" class="form-grid">'
        f'<input type="hidden" name="_csrf" value="{e(csrf)}">'
        f'<label>المهارة<select name="skill_key" required>{options(SKILLS)}</select></label>'
        f'<label>مرحلة التكوين<select name="stage" required>{options(STAGES)}</select></label>'
        f'<label>مستوى الاكتساب<select name="level" required>{options(LEVELS)}</select></label>'
        f'<label>تاريخ التقييم<input type="date" name="evaluated_on" '
        f'min="{year.starts_on.isoformat()}" max="{year.ends_on.isoformat()}" '
        f'value="{date.today().isoformat()}" required></label>'
        '<label>ملاحظة تربوية (اختيارية)<textarea name="comment" '
        'maxlength="600" rows="3"></textarea></label>'
        '<button type="submit">حفظ تقييم المهارة</button></form></section>'
    )
    body = (
        PRINT_STYLE
        + '<section class="skill-sheet">'
        + identity(learner, center, track, year)
        + '<div class="skill-actions">'
        f'<a href="/platform/learners/{en.id}">العودة لملف المستفيد</a>'
        f'<a href="/platform/learners/{en.id}/skills/print">طباعة الشبكة / حفظ PDF</a>'
        '</div>'
        + skill_sections(records)
        + editable
        + '</section>'
    )
    return page(request, user, "شبكة تقييم المهارات", body)


@app.post("/platform/learners/{enrollment_id:int}/skills")
def save_skill_grid(enrollment_id: int, request: Request,
                    token: str = Form(alias="_csrf"),
                    skill_key: str = Form(), stage: str = Form(),
                    level: int = Form(), evaluated_on: date = Form(),
                    comment: str = Form(""),
                    db: Session = Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    en, _, _, _, year = scoped(db, user, enrollment_id)
    if (skill_key not in SKILLS or stage not in STAGES or level not in LEVELS
            or evaluated_on < year.starts_on or evaluated_on > year.ends_on
            or len(comment) > 600):
        raise HTTPException(422, "بيانات التقييم أو التاريخ غير صحيحة")
    if db.scalar(select(SkillEvaluation.id).where(
        SkillEvaluation.enrollment_id == en.id,
        SkillEvaluation.skill_key == skill_key,
        SkillEvaluation.stage == stage,
    )):
        raise HTTPException(
            409, "هذه المهارة مقيمة في المرحلة المحددة. راجع منسق المركز للتصحيح."
        )
    record = SkillEvaluation(
        enrollment_id=en.id, skill_key=skill_key, stage=stage,
        level=level, evaluated_on=evaluated_on,
        comment=comment.strip(), author_id=user["id"],
    )
    db.add(record)
    db.flush()
    log(db, user, "create", "skill_evaluation", record.id)
    db.commit()
    return redirect(f"/platform/learners/{en.id}/skills")


@app.get("/platform/learners/{enrollment_id:int}/skills/print",
         response_class=HTMLResponse)
def print_skill_grid(enrollment_id: int, db: Session = Depends(get_db),
                     user=Depends(actor)):
    en, learner, center, track, year = scoped(db, user, enrollment_id)
    body = (
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>شبكة المهارات — {e(learner.full_name)}</title>'
        + PRINT_STYLE + '</head><body class="skill-print-bg">'
        '<div class="skill-actions skill-print-toolbar">'
        f'<a href="/platform/learners/{en.id}/skills">العودة للتقييم</a>'
        '<button type="button" onclick="window.print()">طباعة / حفظ PDF</button>'
        '</div><main class="skill-sheet skill-paper">'
        + identity(learner, center, track, year)
        + skill_sections(skill_records(db, en.id))
        + '<p class="skill-note">بطاقة تربوية داخلية تعتمد على تقييمات '
        'المستخدمين المخوّلين؛ لا تُعدّ نقطاً مدرسية أو حكماً نهائياً.</p>'
        '</main></body></html>'
    )
    return HTMLResponse(body, headers={"Cache-Control": "private, no-store"})
