"""Approved-only weekly/monthly aggregated reporting."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app, get_db
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
    ids={s.id for s in slots}
    reports=db.scalars(select(SessionReport).where(SessionReport.status=="approved")).all()
    sessions=db.scalars(select(SessionDay).where(SessionDay.held_on>=start,SessionDay.held_on<=end,
        SessionDay.status!="canceled")).all()
    selected=[s for s in sessions if s.slot_id in ids and any(r.session_id==s.id for r in reports)]
    session_ids=[s.id for s in selected]
    att=db.scalars(select(Attendance).where(Attendance.session_id.in_(session_ids))).all() if selected else []
    session_dates={s.id:s.held_on for s in selected}
    absent=[a for a in att if a.status=="absent"]
    present=[a for a in att if a.status in ("present","late")]
    missing=[s for s in selected if not any(a.session_id==s.id for a in att)]
    return dict(sessions=len(selected),recorded=len(selected)-len(missing),
        present=len(present),absent=len(absent),
        absent_days=len({(a.enrollment_id,session_dates[a.session_id]) for a in absent}),
        missing=len(missing))

def stats_html(stats):
    label=[("الحصص المعتمدة",stats["sessions"]),("حُدد حضورها",stats["recorded"]),
      ("الحضور والتأخر",stats["present"]),("حصص الغياب",stats["absent"]),
      ("أيام الغياب (لكل مستفيد)",stats["absent_days"])]
    if stats["missing"]:label.append(("تقارير دون سجل حضور",stats["missing"]))
    return table(["المؤشر","العدد"],[[e(k),e(v)] for k,v in label])

@app.get("/platform/aggregates",response_class=HTMLResponse)
def aggregate_page(request:Request,period:str="weekly",anchor:date|None=None,year_id:int|None=None,
         db:Session=Depends(get_db),user=Depends(actor)):
    anchor=anchor or date.today()
    start,end=period_bounds(period,anchor)
    years=db.scalars(select(Year).order_by(Year.starts_on.desc())).all()
    selected=year_id or next((y.id for y in years if y.active),None) or (years[0].id if years else None)
    if selected and not db.get(Year,selected):raise HTTPException(404,"السنة غير موجودة")
    all_tracks=db.scalars(select(Track).where(Track.year_id==selected)).all() if selected else []
    all_slots=db.scalars(select(Slot).where(Slot.year_id==selected,Slot.canceled.is_(False))).all() if selected else []
    centers=db.scalars(select(Center).order_by(Center.id)).all()
    params='<form action="/platform/aggregates" method="get" class="form-grid">'+(
        '<label>نوع الفترة<select name="period"><option value="weekly"'+(" selected" if period=="weekly" else "")+
        '>أسبوعي</option><option value="monthly"'+(" selected" if period=="monthly" else "")+'>شهري</option></select></label>'+
        f'<label>التاريخ المرجعي<input type="date" name="anchor" value="{anchor.isoformat()}"></label>'+
        '<label>السنة التكوينية<select name="year_id">'+
        "".join(f'<option value="{y.id}" {"selected" if y.id==selected else ""}>{e(y.label)}</option>' for y in years)+
        '</select></label><button type="submit">عرض الإحصائيات</button><button type="button" onclick="window.print()">طباعة / حفظ PDF</button></form>')
    body=f'<section>{params}<p>المدة: من <b>{start}</b> إلى <b>{end}</b> · الإحصائيات مأخوذة فقط من تقارير الحصص المعتمدة.</p></section>'
    accessible_centers=[]
    for center in centers:
        if not center_allowed(user,center.id):continue
        tracks=[t for t in all_tracks if t.center_id==center.id]
        if user["role"] in ("instructor","educator"):
            ids={a.track_id for a in user["assignments"]}
            tracks=[t for t in tracks if t.id in ids]
        if not tracks:continue
        relevant=[s for s in all_slots if s.track_id in {t.id for t in tracks}]
        stat=approved_stats(db,relevant,start,end)
        body+=f'<section><h2>{e(center.name)}</h2>{stats_html(stat)}</section>'
        for t in tracks:
            ss=[s for s in relevant if s.track_id==t.id]
            body+=f'<section><h2>المسلك: {e(t.name)}</h2>{stats_html(approved_stats(db,ss,start,end))}</section>'
        if user["role"] in ("admin", "coordinator"):
            selected = []
            for evidence in db.scalars(select(Attachment).where(Attachment.select_for_summary.is_(True))).all():
                report = db.get(SessionReport, evidence.report_id)
                if not report or report.status != "approved":
                    continue
                session = db.get(SessionDay, report.session_id)
                if (session and start <= session.held_on <= end
                        and session.slot_id in {item.id for item in relevant}):
                    selected.append(evidence)
            if selected:
                body += '<section><h2>مرفقات مختارة من المنسق</h2>'
                documents = []
                for evidence in selected:
                    link = f'/platform/attachments/{evidence.id}/download'
                    if evidence.content_type.startswith("image/"):
                        body += (f'<figure><img alt="{e(evidence.original_name)}" '
                                 f'src="/platform/attachments/{evidence.id}/view" '
                                 'style="max-width:95%;max-height:260px">'
                                 f'<figcaption>{e(evidence.original_name)}</figcaption></figure>')
                    else:
                        documents.append(f'<li><a href="{link}">{e(evidence.original_name)}</a></li>')
                if documents:
                    body += '<h3>الوثائق المرفقة في الملحق</h3><ul>' + ''.join(documents) + '</ul>'
                body += '</section>'
        accessible_centers.append(center.id)
    if user["role"]=="admin" and accessible_centers:
        combined=[s for s in all_slots if s.center_id in accessible_centers]
        body+=f'<section><h2>إجمالي الجمعية (دون مقارنة بين المركزين)</h2>{stats_html(approved_stats(db,combined,start,end))}</section>'
    if not accessible_centers:body+='<section>لا توجد مسالك ضمن النطاق المتاح لهذه السنة.</section>'
    return page(request,user,"التقارير الأسبوعية والشهرية",body)
