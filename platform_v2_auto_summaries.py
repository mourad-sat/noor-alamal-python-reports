"""Generate editable weekly/monthly center drafts from approved session evidence.

Never fabricate narrative: copy only approved report fields with their source dates.
Aggregate statistics are restricted to approved sessions in the chosen center/year.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
from platform_v2 import (actor, assert_csrf, center_allowed, coordinator, e,
                         field, form, page, require_role, redirect, log)
from platform_v2_aggregates import approved_stats, period_bounds
from platform_v2_models import (Center, CenterReport, SessionDay, SessionReport,
                                Slot, Track, Year)


def approved_evidence(db: Session, center_id: int, year_id: int,
                      start: date, end: date):
    """Only real, non-canceled sessions with approved reports in this center/year."""
    return db.execute(
        select(SessionDay, SessionReport, Slot, Track)
        .join(SessionReport, SessionReport.session_id == SessionDay.id)
        .join(Slot, Slot.id == SessionDay.slot_id)
        .join(Track, Track.id == Slot.track_id)
        .where(Slot.center_id == center_id,
               Slot.year_id == year_id,
               Track.center_id == center_id,
               Track.year_id == year_id,
               SessionDay.held_on >= start,
               SessionDay.held_on <= end,
               SessionDay.status != "canceled",
               SessionReport.status == "approved")
        .order_by(SessionDay.held_on, SessionDay.id)
    ).all()


def build_draft(db: Session, center_id: int, year_id: int,
                start: date, end: date) -> str:
    evidence = approved_evidence(db, center_id, year_id, start, end)
    slots = db.scalars(select(Slot).where(
        Slot.center_id == center_id, Slot.year_id == year_id)).all()
    # approved_stats only counts approved actual sessions; no planned slots as work.
    stats = approved_stats(db, slots, start, end)
    lines = [
        "حصيلة الفترة اعتماداً على تقارير الحصص المصادق عليها:",
        f"عدد الحصص المنجزة والمعتمدة: {stats['sessions']}",
        f"الحصص التي تتوفر على تسجيل حضور: {stats['recorded']}",
        f"سجلات الحضور والتأخر: {stats['present']}",
        f"سجلات الغياب: {stats['absent']}",
        f"أيام الغياب بحسب المستفيد: {stats['absent_days']}",
    ]
    if stats["missing"]:
        lines.append(f"حصص معتمدة دون تسجيل حضور: {stats['missing']}")
    sections = [
        ("الأنشطة المنجزة", "activities"),
        ("الأهداف", "objectives"),
        ("النتائج المحققة", "results"),
        ("الصعوبات", "challenges"),
        ("التوصيات", "recommendations"),
    ]
    for heading, attr in sections:
        lines.extend(("", heading + ":"))
        filled = 0
        for session, report, _slot, track in evidence:
            value = (getattr(report, attr) or "").strip()
            if not value:
                continue
            filled += 1
            # Traceable provenance, rather than invented narrative synthesis.
            snippet = " ".join(value.split())[:360]
            lines.append(
                f"- {session.held_on.isoformat()} | {track.name} | "
                f"تقرير الحصة #{report.id}: {snippet}"
            )
        if not filled:
            lines.append("لا تتوفر معطيات مصادق عليها في هذا المحور.")
    if not evidence:
        lines.extend(("", "ملاحظة: لا توجد تقارير حصص مصادق عليها في هذه الفترة؛ "
                       "هذه مسودة إحصائية فارغة ولا تثبت إنجاز أي حصة."))
    lines.extend(("", "هذه مسودة آلية قابلة لمراجعة المنسق قبل المصادقة الإدارية."))
    # Existing report model has a 12k-character analysis field.
    result = "\n".join(lines)
    if len(result) > 12000:
        result = result[:11800].rsplit("\n", 1)[0] + (
            "\n[تم اختصار المحتوى بسبب طول تقارير الحصص؛ "
            "يرجى مراجعة التقارير الأصلية.]"
        )
    return result


def scoped_summary(db: Session, user, report_id: int) -> CenterReport:
    report = db.get(CenterReport, report_id)
    if not report:
        raise HTTPException(404, "التقرير الدوري غير موجود")
    if not center_allowed(user, report.center_id):
        raise HTTPException(403, "غير مسموح بالاطلاع على هذا التقرير")
    return report


@app.post("/platform/summary/auto")
def create_generated_summary(request: Request, token: str = Form(alias="_csrf"),
                             year_id: int = Form(), center_id: int = Form(),
                             period_type: str = Form(), anchor: date = Form(),
                             db: Session = Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    coordinator(user, center_id)
    year = db.get(Year, year_id)
    if year is None or period_type not in ("weekly", "monthly"):
        raise HTTPException(422, "السنة أو نوع التقرير غير صحيح")
    start, end = period_bounds(period_type, anchor)
    if not (year.starts_on <= start <= end <= year.ends_on):
        raise HTTPException(422, "الفترة خارج السنة التكوينية المختارة")
    if not db.scalar(select(Track.id).where(
            Track.center_id == center_id, Track.year_id == year_id).limit(1)):
        raise HTTPException(422, "لا يوجد مسلك لهذا المركز والسنة")
    analysis = build_draft(db, center_id, year_id, start, end)
    report = CenterReport(
        year_id=year_id, center_id=center_id, period_type=period_type,
        period_start=start, period_end=end, analysis=analysis,
        status="draft", author_id=user["id"],
    )
    db.add(report)
    db.flush()
    log(db, user, "auto_generate", "center_report", report.id)
    db.commit()
    return redirect(f"/platform/summary/{report.id}/edit")


@app.get("/platform/summary/{report_id:int}/edit", response_class=HTMLResponse)
def edit_generated_summary(request: Request, report_id: int,
                           db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    report = scoped_summary(db, user, report_id)
    if report.author_id != user["id"] or report.status not in ("draft", "returned"):
        raise HTTPException(403, "هذا التقرير غير قابل للتعديل من حسابك")
    center = db.get(Center, report.center_id)
    title = "تعديل ومراجعة مسودة التقرير الدوري"
    body = (
        '<section><h2>مراجعة التقرير قبل إرساله للمصادقة</h2>'
        f'<p>{e(center.name)} · {e(report.period_start)} إلى {e(report.period_end)}</p>'
        '<p class="subtle">النص الآتي مستخرج من تقارير الحصص المعتمدة '
        'دون اختلاق وقائع جديدة. راجع المعلومات وأضف تحليلك وتوصياتك قبل الإرسال.</p>'
        + form(f"/platform/summary/{report.id}/edit", request.session["_csrf"] if
               "_csrf" in request.session else request.session["csrf"],
               '<label>محتوى التقرير<textarea name="analysis" rows="24" '
               'maxlength="12000" required>'
               + e(report.analysis) + '</textarea></label>', "حفظ التعديلات")
        + f'<p><a class="button" href="/platform/summary/{report.id}/print">'
          'معاينة وطباعة المسودة</a></p>'
        + '</section>'
    )
    return page(request, user, title, body)


@app.post("/platform/summary/{report_id:int}/edit")
def save_generated_summary(request: Request, report_id: int,
                           token: str = Form(alias="_csrf"),
                           analysis: str = Form(),
                           db: Session = Depends(get_db), user=Depends(actor)):
    assert_csrf(request, token)
    report = scoped_summary(db, user, report_id)
    coordinator(user, report.center_id)
    if report.author_id != user["id"] or report.status not in ("draft", "returned"):
        raise HTTPException(403, "لا يمكن تعديل تقرير مرسل أو معتمد")
    if not analysis.strip() or len(analysis) > 12000:
        raise HTTPException(422, "يجب كتابة محتوى صحيح للتقرير")
    report.analysis = analysis.strip()
    log(db, user, "update", "center_report", report.id, "تحرير مسودة التقرير الدوري")
    db.commit()
    return redirect(f"/platform/summary/{report.id}/edit")


@app.get("/platform/summary/{report_id:int}/print", response_class=HTMLResponse)
def print_generated_summary(report_id: int, db: Session = Depends(get_db),
                            user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    report = scoped_summary(db, user, report_id)
    center = db.get(Center, report.center_id)
    year = db.get(Year, report.year_id)
    period_label = {"weekly": "الأسبوعي", "monthly": "الشهري",
                    "annual": "السنوي", "activity": "الخاص بالأنشطة"}.get(
                        report.period_type, "الدوري")
    is_approved = report.status == "approved"
    stamp = (
        f'<p class="approval">تمت المصادقة من طرف إدارة الجمعية · {e(report.reviewed_by)}</p>'
        if is_approved else
        '<p class="draft">مسودة غير مصادق عليها — للاطلاع والمراجعة فقط</p>'
    )
    paragraphs = "".join(
        f'<p>{e(line) if line.strip() else "&nbsp;"}</p>'
        for line in report.analysis.splitlines()
    )
    html = f"""<!doctype html><html dir="rtl" lang="ar"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>التقرير {e(period_label)} — {e(center.name)}</title>
    <style>
    @page{{size:A4 portrait;margin:15mm}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:#eaf1f5;color:#203847;font-family:Tahoma,Arial,sans-serif;line-height:1.75}}
    .controls{{padding:12px 16px;max-width:210mm;margin:12px auto;
      display:flex;justify-content:space-between;align-items:center;gap:12px;
      border-radius:10px;background:#173f53;color:#fff}}
    .controls a{{color:#aaf5e8}}
    button{{padding:8px 14px;border:0;border-radius:7px;background:#197f71;color:#fff;cursor:pointer}}
    .paper{{max-width:210mm;min-height:270mm;margin:12px auto;padding:15mm 12mm;
      background:white;box-shadow:0 6px 26px #14354a25}}
    header{{display:flex;align-items:center;gap:15px;padding-bottom:12px;border-bottom:3px solid #238c7e}}
    header img{{width:68px;height:68px;object-fit:contain}}
    header strong{{font-size:20px;color:#195769}}
    header small{{display:block;color:#577385}}
    h1{{font-size:22px;color:#174e60;text-align:center;margin:20px 0 6px}}
    .details{{background:#f2f7f8;border:1px solid #cee1e5;padding:10px 15px;font-size:12px}}
    .content{{font-size:12px;margin-top:15px;white-space:normal}}
    .content p{{margin:0 0 7px;overflow-wrap:anywhere}}
    .approval{{border-top:1px solid #bdd9d9;padding-top:12px;font-weight:bold;color:#087868}}
    .draft{{padding:12px;background:#fff1e7;border:1px solid #e0b998;color:#955232}}
    .signatures{{display:flex;justify-content:space-between;gap:16px;margin-top:26px;font-size:12px}}
    @media print{{body{{background:white}}.controls{{display:none}}.paper{{max-width:none;
      min-height:0;margin:0;padding:0;box-shadow:none}}.details,header{{break-inside:avoid}}}}
    </style></head><body>
    <nav class="controls"><a href="/platform/reports">العودة للتقارير</a>
    <button type="button" onclick="window.print()">طباعة / حفظ PDF</button></nav>
    <main class="paper">
    <header><img src="/static/noor_official_logo.webp" alt="شعار الجمعية">
    <div><strong>جمعية نور الأمل</strong><small>برنامج الفرصة الثانية – الجيل الجديد</small></div></header>
    <h1>التقرير {e(period_label)} لأنشطة المركز</h1>
    <div class="details">المركز: {e(center.name)} · السنة: {e(year.label)} ·
    من: {e(report.period_start)} إلى: {e(report.period_end)} · رقم التقرير: {report.id}</div>
    <div class="content">{paragraphs}</div>{stamp}
    {('<div class="signatures"><span>توقيع منسق المركز: ................</span>'
      '<span>ختم ومصادقة الجمعية: ................</span></div>' if is_approved else '')}
    </main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})
