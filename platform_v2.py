"""Arabic operational portal for the two Noor Al Amal centers.

All writes are CSRF protected. Access scope is enforced on the server; hiding a
link in the UI is never considered authorization.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import app, check_csrf, get_db, verify_password
from platform_v2_models import (
    Center, Year, Track, Staff, Assignment, Learner, Enrollment, Slot, Closure,
    SessionDay, Attendance, Permit, SessionReport, Assessment, Grade,
    Incident, Discipline, Notice, Notification, Audit, now,
)

SUBJECTS = ("الرياضيات", "الفرنسية", "العربية")
TRACK_NAMES = {
    "دار الشباب لعيايدة 2": ("التصميم وبناء الهوية", "التسويق الإلكتروني", "التنشيط وإقامة الفعاليات"),
    "مركز حمزة عبد المطلب": ("التصميم وبناء الهوية", "التسويق الإلكتروني", "صيانة الأجهزة الإلكترونية"),
}
ROLE_LABELS = {"admin": "إدارة الجمعية", "coordinator": "منسق المركز", "instructor": "مكوّن مهني", "educator": "منشط التأهيل التربوي"}
STATUS_LABELS = {"draft": "مسودة", "submitted": "بانتظار المصادقة", "approved": "معتمد", "returned": "أعيد للتصحيح", "pending": "بانتظار المصادقة",
                 "issued": "صدر", "delivered": "تم التسليم", "deferred": "مؤجل", "pending_admin": "بانتظار إدارة الجمعية", "effective": "نافذ"}
def e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)

def redirect(path: str = "/platform"):
    return RedirectResponse(path, status_code=303)

def assert_csrf(request: Request, token: str):
    check_csrf(request, token)

def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 256:
        raise HTTPException(422, "يجب أن تتكون كلمة المرور من 12 حرفًا على الأقل.")
    salt = secrets.token_bytes(16)
    rounds = 390000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"

def authenticate_staff(db: Session, username: str, password: str):
    user = db.scalar(select(Staff).where(Staff.username == username.strip()))
    if user and user.enabled and verify_password(password, user.password_hash):
        return user
    return None

def actor(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(303, headers={"Location": "/login"})
    if request.session.get("role") == "admin":
        return {"role": "admin", "name": "إدارة الجمعية", "id": None, "assignments": []}
    uid = request.session.get("staff_id")
    user = db.get(Staff, uid) if isinstance(uid, int) else None
    if not user or not user.enabled or user.session_version != request.session.get("session_version"):
        raise HTTPException(403, "الحساب معطل أو انتهت صلاحية الجلسة. يرجى إعادة تسجيل الدخول.")
    return {"role": user.role, "name": user.full_name, "id": user.id,
            "assignments": db.scalars(select(Assignment).where(Assignment.staff_id == user.id)).all()}

def require_role(user, *roles):
    if user["role"] not in roles:
        raise HTTPException(403, "ليس لديك صلاحية تنفيذ هذه العملية.")

def center_allowed(user, center_id: int) -> bool:
    return user["role"] == "admin" or any(a.center_id == center_id for a in user["assignments"])

def coordinator(user, center_id: int):
    if user["role"] != "coordinator" or not center_allowed(user, center_id):
        raise HTTPException(403, "هذه العملية من صلاحيات منسق المركز فقط.")

def learner_scope(db: Session, user, enrollment_id: int):
    enrollment = db.get(Enrollment, enrollment_id)
    if not enrollment:
        raise HTTPException(404, "المستفيد غير موجود")
    if user["role"] == "admin":
        return enrollment
    if user["role"] == "coordinator" and center_allowed(user, enrollment.center_id):
        return enrollment
    if any(a.center_id == enrollment.center_id and a.track_id == enrollment.track_id for a in user["assignments"]):
        return enrollment
    raise HTTPException(403, "لا يحق لك الاطلاع على ملف هذا المستفيد.")

def slot_scope(db: Session, user, slot_id: int, for_write=False):
    slot = db.get(Slot, slot_id)
    if not slot:
        raise HTTPException(404, "الحصة غير موجودة")
    if user["role"] == "admin" and not for_write:
        return slot
    if user["role"] == "coordinator" and center_allowed(user, slot.center_id) and not for_write:
        return slot
    if user["id"] == slot.staff_id and any(a.center_id == slot.center_id and a.track_id == slot.track_id for a in user["assignments"]):
        return slot
    raise HTTPException(403, "ليس لديك صلاحية الوصول لهذه الحصة.")

def log(db: Session, user, action: str, resource: str, resource_id: int, reason: str = ""):
    db.add(Audit(actor=user["name"], action=action, resource=resource, resource_id=resource_id, reason=reason[:1000]))

def notify(db: Session, center_id: int, message: str, target: str):
    coordinators = db.scalars(select(Assignment).join(Staff, Assignment.staff_id == Staff.id).where(
        Assignment.center_id == center_id, Staff.role == "coordinator", Staff.enabled.is_(True))).all()
    for a in coordinators:
        db.add(Notification(staff_id=a.staff_id, message=message[:300], target=target[:255]))

def post_fields(csrf: str):
    return f'<input type="hidden" name="_csrf" value="{e(csrf)}">'

def form(action, csrf, fields, button="حفظ"):
    return f'<form method="post" action="{e(action)}" class="form-grid">{post_fields(csrf)}{fields}<button type="submit">{e(button)}</button></form>'

def field(name, label, typ="text", required=True, value="", extra=""):
    return f'<label>{e(label)}<input name="{e(name)}" type="{e(typ)}" value="{e(value)}" {"required" if required else ""} {extra}></label>'

def area(name, label):
    return f'<label>{e(label)}<textarea name="{e(name)}" rows="2"></textarea></label>'

def choose(name, label, values, selected=None):
    pairs = values.items() if isinstance(values, dict) else values
    return f'<label>{e(label)}<select name="{e(name)}">' + "".join(
        f'<option value="{e(value)}" {"selected" if str(value)==str(selected) else ""}>{e(title)}</option>'
        for value, title in pairs
    ) + '</select></label>'

def table(headers, rows):
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return '<div class="table-scroll"><table><thead><tr>' + "".join(f"<th>{e(h)}</th>" for h in headers) + "</tr></thead><tbody>" + (body or f'<tr><td colspan="{len(headers)}">لا توجد بيانات بعد.</td></tr>') + "</tbody></table></div>"

def page(request, user, title, body):
    menu = [
        ("الرئيسية", "/platform"), ("السنوات والمسالك", "/platform/years"),
        ("المستفيدون", "/platform/learners"), ("جدولة الحصص", "/platform/schedule"),
        ("تقارير الحصص", "/platform/reports"), ("تقارير أسبوعية وشهرية", "/platform/aggregates"), ("النقط", "/platform/grades"),
        ("السلوك والانضباط", "/platform/discipline"), ("الإشعارات الورقية", "/platform/notices"),
        ("التنبيهات", "/platform/notifications"),
    ]
    if user["role"] != "admin":
        menu.append(("تغيير كلمة المرور", "/platform/password"))
    if user["role"] in ("admin","coordinator"):
        menu.append(("استيراد Excel", "/platform/import"))
        menu.append(("كشوف النقط PDF", "/platform/exports/grades"))
    if user["role"] == "admin":
        menu.insert(1, ("الموظفون", "/platform/staff"))
        menu.append(("إعدادات التنبيه", "/platform/settings"))
        menu.append(("السجل الإداري", "/platform/audit"))
        menu.append(("التقارير القديمة", "/reports"))
    nav = "".join(f'<a href="{url}">{e(label)}</a>' for label, url in menu)
    return HTMLResponse(f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | جمعية نور الأمل</title><link rel="stylesheet" href="/static/style.css">
<style>
.pv2 {{min-height:100vh;background:#f6f9fc;color:#14283e;font-family:Tahoma,Arial,sans-serif}}
.pv2 nav {{background:#125083;color:white;padding:16px;display:flex;flex-wrap:wrap;gap:12px}}
.pv2 nav a {{color:white;text-decoration:none;padding:5px 9px;border-radius:8px}}
.pv2 nav a:hover {{background:#ffffff24}}
.pv2 main {{max-width:1150px;margin:24px auto;padding:0 16px 50px}}
.pv2 section {{background:white;border:1px solid #dce8f0;border-radius:12px;margin:18px 0;padding:18px;box-shadow:0 3px 12px #0a244407}}
.pv2 .form-grid {{display:flex;align-items:end;flex-wrap:wrap;gap:12px}}
.pv2 label {{display:grid;gap:5px;font-size:14px;min-width:160px;flex:1}}
.pv2 input,.pv2 textarea,.pv2 select {{width:100%;border:1px solid #b7ccdc;padding:10px;border-radius:7px;background:white;color:#14283e}}
.pv2 button,.pv2 .button {{background:#12609a;color:white;border:0;border-radius:7px;padding:11px 15px;text-decoration:none;cursor:pointer}}
.pv2 table {{width:100%;border-collapse:collapse;background:white}} .pv2 th,.pv2 td {{padding:10px;text-align:right;border-bottom:1px solid #e1e9ef}}
.pv2 .table-scroll {{overflow:auto}} .pv2 .subtle {{color:#526578;font-size:13px}}
.pv2 .danger {{color:#ad2735}} .pv2 .good {{color:#16813d}}
.pv2 h1 {{font-size:25px}} .pv2 h2 {{font-size:19px}}
@media print {{.pv2 nav,.pv2 button,.pv2 form,.pv2 .no-print {{display:none!important}} .pv2 main {{margin:0;max-width:none}}}}
</style></head><body class="pv2">
<nav><strong>جمعية نور الأمل · الفرصة الثانية</strong>{nav}<span>{e(user["name"])} ({e(ROLE_LABELS[user["role"]])})</span>
<form method="post" action="/logout">{post_fields(request.session.get("csrf",""))}<button>خروج</button></form></nav>
<main><h1>{e(title)}</h1>{body}</main></body></html>''')

def center_options(db, user):
    rows = db.scalars(select(Center).order_by(Center.id)).all()
    return [(c.id, c.name) for c in rows if center_allowed(user, c.id)]

def tracks_options(db, user):
    rows = db.scalars(select(Track).order_by(Track.center_id, Track.name)).all()
    return [(t.id, f"{t.name} · {db.get(Center,t.center_id).name} · {db.get(Year,t.year_id).label}") for t in rows if center_allowed(user,t.center_id)]

def ensure_data(db):
    if not db.scalar(select(func.count()).select_from(Center)):
        for name, area in (("دار الشباب لعيايدة 2", "لعيايدة"), ("مركز حمزة عبد المطلب", "بطانة")):
            db.add(Center(name=name, area=area))
        db.commit()

@app.get("/platform", response_class=HTMLResponse)
def home(request: Request, db: Session=Depends(get_db), user=Depends(actor)):
    ensure_data(db)
    center_ids = [x[0] for x in center_options(db,user)]
    enrollments = db.scalars(select(Enrollment).where(Enrollment.center_id.in_(center_ids))).all()
    pending = db.scalars(select(Attendance).join(Enrollment, Attendance.enrollment_id == Enrollment.id).where(
        Enrollment.center_id.in_(center_ids), Attendance.status == "absent", Attendance.permit_id.is_(None))).all()
    notices = db.scalars(select(Notice).join(Enrollment, Notice.enrollment_id == Enrollment.id).where(
        Enrollment.center_id.in_(center_ids), Notice.status == "draft")).all()
    numbers = f'<section><h2>مؤشرات العمل</h2><p>المستفيدون المسجلون: <strong>{len(enrollments)}</strong> · حالات الغياب المعلقة: <strong class="danger">{len(pending)}</strong> · مسودات الإشعارات الورقية: <strong>{len(notices)}</strong></p><p class="subtle">حالة الغياب باللون الأحمر تعني وجود غياب لم تُصدر بشأنه رخصة دخول. الإقصاء التأديبي حالة منفصلة.</p></section>'
    blocks = []
    for c in db.scalars(select(Center)).all():
        if c.id not in center_ids: continue
        ts = db.scalars(select(Track).where(Track.center_id==c.id)).all()
        blocks.append(f'<section><h2>{e(c.name)} · {e(c.area)}</h2><p>المستفيدون: {sum(x.center_id==c.id for x in enrollments)} · المسالك المهيأة: {len(ts)}</p></section>')
    return page(request,user,"لوحة القيادة",numbers+"".join(blocks))

@app.get("/platform/years", response_class=HTMLResponse)
def years_page(request:Request, db:Session=Depends(get_db), user=Depends(actor)):
    require_role(user,"admin")
    ensure_data(db)
    csrf=request.session["csrf"]
    years=db.scalars(select(Year).order_by(Year.starts_on.desc())).all()
    tracks=db.scalars(select(Track).order_by(Track.year_id.desc(),Track.center_id)).all()
    yform=form("/platform/years",csrf,field("label","السنة التكوينية",value="2026/2027")+field("starts_on","البداية","date")+field("ends_on","النهاية","date"),"إنشاء سنة جديدة من الصفر")
    tform=form("/platform/tracks",csrf,choose("year_id","السنة",[(y.id,y.label) for y in years])+choose("center_id","المركز",center_options(db,user))+field("name","اسم المسلك"),"إضافة مسلك")
    yrows=[[e(y.label),e(y.starts_on),e(y.ends_on),e("مؤرشفة" if y.archived else "مفعلة" if y.active else "غير مفعلة"),
        (form(f"/platform/years/{y.id}/activate",csrf,"","تفعيل")+
         form(f"/platform/years/{y.id}/archive",csrf,area("reason","مبرر الأرشفة"),"أرشفة")) if not y.archived else "مؤرشفة"] for y in years]
    trows=[[e(db.get(Year,t.year_id).label),e(db.get(Center,t.center_id).name),e(t.name)] for t in tracks]
    return page(request,user,"السنوات والمسالك",f"<section><h2>السنوات</h2>{yform}{table(['السنة','من','إلى','الحالة','الإجراء'],yrows)}</section><section><h2>المسالك المستقلة لكل مركز</h2>{tform}{table(['السنة','المركز','المسلك'],trows)}</section>")

@app.post("/platform/years")
def year_create(request:Request, token:str=Form(alias="_csrf"),label:str=Form(),starts_on:date=Form(),ends_on:date=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    if starts_on>=ends_on or not label.strip():raise HTTPException(422,"السنة أو تاريخا البداية والنهاية غير صحيحين")
    y=Year(label=label.strip()[:40],starts_on=starts_on,ends_on=ends_on,active=False)
    db.add(y)
    try:db.flush();log(db,user,"create","year",y.id);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"السنة موجودة بالفعل")
    return redirect("/platform/years")

@app.post("/platform/years/{year_id}/activate")
def year_activate(year_id:int,request:Request,token:str=Form(alias="_csrf"),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    y=db.get(Year,year_id)
    if not y or y.archived:raise HTTPException(422,"السنة غير متاحة")
    for other in db.scalars(select(Year)).all():other.active=False
    y.active=True;log(db,user,"activate","year",y.id);db.commit()
    return redirect("/platform/years")

@app.post("/platform/years/{year_id}/archive")
def year_archive(year_id:int,request:Request,token:str=Form(alias="_csrf"),reason:str=Form(),
                 db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    if len(reason.strip())<6:raise HTTPException(422,"يجب توضيح سبب الأرشفة")
    year=db.get(Year,year_id)
    if not year or year.archived:raise HTTPException(409,"السنة غير متاحة للأرشفة")
    year.active=False;year.archived=True
    log(db,user,"archive","year",year.id,reason);db.commit()
    return redirect("/platform/years")

@app.post("/platform/tracks")
def track_create(request:Request,token:str=Form(alias="_csrf"),year_id:int=Form(),center_id:int=Form(),name:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    if not db.get(Year,year_id) or not db.get(Center,center_id) or not name.strip():raise HTTPException(422,"بيانات المسلك غير صحيحة")
    t=Track(year_id=year_id,center_id=center_id,name=name.strip()[:140])
    db.add(t)
    try:db.flush();log(db,user,"create","track",t.id);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"المسلك موجود في هذا المركز والسنة")
    return redirect("/platform/years")

@app.get("/platform/staff",response_class=HTMLResponse)
def staff_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");ensure_data(db);csrf=request.session["csrf"]
    staff=db.scalars(select(Staff).order_by(Staff.id)).all()
    add=form("/platform/staff",csrf,field("username","اسم المستخدم")+field("full_name","الاسم الكامل")+choose("role","الدور",[(k,v) for k,v in ROLE_LABELS.items() if k!="admin"])+field("password","كلمة مرور أولية (12 حرفًا على الأقل)","password"),"إنشاء حساب")
    assign=form("/platform/assign",csrf,choose("staff_id","الموظف",[(s.id,s.full_name) for s in staff])+choose("center_id","المركز",center_options(db,user))+choose("track_id","المسلك (0 للمنسق)",[(0,"كل المركز / منسق")]+tracks_options(db,user))+choose("subject","المادة",[("","التكوين المهني / إدارة المركز")]+[(s,s) for s in SUBJECTS]),"إسناد العمل")
    rows=[[e(s.full_name),e(s.username),e(ROLE_LABELS.get(s.role,s.role)),e("نشط" if s.enabled else "معطل"),
        form(f"/platform/staff/{s.id}/toggle",csrf, "", "تعطيل" if s.enabled else "تفعيل")+
        form(f"/platform/staff/{s.id}/password",csrf,field("password","كلمة مرور جديدة","password"),"إعادة التعيين")] for s in staff]
    return page(request,user,"حسابات الموظفين",f"<section><h2>إنشاء حساب بإذن الإدارة فقط</h2>{add}</section><section><h2>الربط بالمركز والمسلك والمادة</h2>{assign}</section><section>{table(['الاسم','المستخدم','الدور','الحالة','الإدارة'],rows)}</section>")

@app.post("/platform/staff")
def create_staff(request:Request,token:str=Form(alias="_csrf"),username:str=Form(),full_name:str=Form(),role:str=Form(),password:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    if role not in ("coordinator","instructor","educator") or not username.strip() or not full_name.strip():raise HTTPException(422,"بيانات الحساب غير صحيحة")
    s=Staff(username=username.strip()[:80],full_name=full_name.strip()[:140],role=role,password_hash=hash_password(password))
    db.add(s)
    try:db.flush();log(db,user,"create","staff",s.id);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"اسم المستخدم موجود")
    return redirect("/platform/staff")

@app.post("/platform/staff/{staff_id}/toggle")
def toggle_staff(staff_id:int,request:Request,token:str=Form(alias="_csrf"),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    s=db.get(Staff,staff_id)
    if not s:raise HTTPException(404)
    s.enabled=not s.enabled;log(db,user,"staff_toggle","staff",s.id,str(s.enabled));db.commit()
    return redirect("/platform/staff")

@app.post("/platform/staff/{staff_id}/password")
def change_staff_password(staff_id:int,request:Request,token:str=Form(alias="_csrf"),password:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    s=db.get(Staff,staff_id)
    if not s:raise HTTPException(404)
    s.password_hash=hash_password(password);s.session_version+=1;log(db,user,"password_reset","staff",s.id);db.commit()
    return redirect("/platform/staff")

@app.post("/platform/assign")
def assign_staff(request:Request,token:str=Form(alias="_csrf"),staff_id:int=Form(),center_id:int=Form(),track_id:int=Form(),subject:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    s=db.get(Staff,staff_id)
    if not s or not db.get(Center,center_id):raise HTTPException(422,"المركز أو الموظف غير صحيح")
    t=db.get(Track,track_id) if track_id else None
    if s.role=="coordinator":
        if t or subject:raise HTTPException(422,"المنسق يتبع المركز بكامله")
        current=db.scalar(select(Assignment).join(Staff,Assignment.staff_id==Staff.id).where(
            Assignment.center_id==center_id,Staff.role=="coordinator"))
        if current and current.staff_id!=s.id:raise HTTPException(409,"هذا المركز له منسق بالفعل")
        if db.scalar(select(Assignment).where(Assignment.staff_id==s.id,Assignment.center_id!=center_id)):
            raise HTTPException(409,"لكل منسق مركز واحد")
    else:
        if not t or t.center_id!=center_id:raise HTTPException(422,"اختر مسلكًا من المركز")
        if (s.role=="instructor" and subject) or (s.role=="educator" and subject not in SUBJECTS):
            raise HTTPException(422,"المادة لا تناسب نوع الموظف")
        if s.role=="instructor":
            assigned=db.scalar(select(Assignment).join(Staff,Assignment.staff_id==Staff.id).where(
                Assignment.track_id==track_id,Staff.role=="instructor"))
            if assigned and assigned.staff_id!=s.id:raise HTTPException(409,"المسلك له مكوّن مهني مسؤول")
    a=Assignment(staff_id=s.id,center_id=center_id,track_id=t.id if t else None,subject=subject)
    db.add(a)
    try:db.flush();log(db,user,"assign","staff",s.id);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"هذا الإسناد موجود")
    return redirect("/platform/staff")


@app.get("/platform/learners",response_class=HTMLResponse)
def learners_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    ensure_data(db)
    csrf=request.session["csrf"]
    centers=center_options(db,user)
    enrolls=db.scalars(select(Enrollment).order_by(Enrollment.id.desc()).limit(800)).all()
    enrolls=[en for en in enrolls if center_allowed(user,en.center_id) and (
        user["role"] in ("admin","coordinator") or any(a.track_id==en.track_id for a in user["assignments"]))]
    rows=[]
    for en in enrolls:
        l=db.get(Learner,en.learner_id)
        pend=db.scalar(select(func.count()).select_from(Attendance).where(Attendance.enrollment_id==en.id,
                        Attendance.status=="absent",Attendance.permit_id.is_(None))) or 0
        disciplinary=disciplinary_status(db,en)
        state='<span class="danger">غياب معلق</span>' if pend else '<span class="good">لا غياب معلق</span>'
        if disciplinary:state+=' · <span class="danger">إقصاء تأديبي</span>'
        rows.append([f'<a href="/platform/learners/{en.id}">{e(l.full_name)}</a>',e(db.get(Center,en.center_id).name),
                     e(db.get(Track,en.track_id).name),state])
    body=f"<section>{table(['المستفيد','المركز','المسلك','الحالة'],rows)}</section>"
    if user["role"] in ("admin","coordinator"):
        years=db.scalars(select(Year)).all()
        body='<section><h2>تسجيل مستفيد في سنة ومسلك واحد</h2>'+form("/platform/learners",csrf,
            field("full_name","اسم المستفيد")+choose("gender","الجنس",[("","غير محدد"),("ذكر","ذكر"),("أنثى","أنثى")])+
            field("born_on","تاريخ الازدياد","date",False)+field("birthplace","مكان الازدياد",required=False)+
            field("phone","هاتف المستفيد",required=False)+field("address","العنوان",required=False)+
            field("guardian_name","اسم ولي الأمر",required=False)+field("guardian_relationship","الصفة",required=False)+
            field("guardian_phone","هاتف ولي الأمر للقاصر",required=False)+
            choose("year_id","السنة",[(y.id,y.label) for y in years if not y.archived])+
            choose("track_id","المسلك",tracks_options(db,user)),"تسجيل")+'</section>'+body
    return page(request,user,"ملفات المستفيدين",body)

def disciplinary_status(db:Session,en:Enrollment,at:date|None=None):
    at=at or date.today()
    records=db.scalars(select(Discipline).where(Discipline.enrollment_id==en.id,
                                               Discipline.status.in_(("effective","early_pending")))).all()
    for d in records:
        if not d.return_authorized and (d.excluded_from is None or d.excluded_from<=at):
            # At the end date, a separate reasoned return authorization remains mandatory.
            return True
    return False

@app.post("/platform/learners")
def learner_create(request:Request,token:str=Form(alias="_csrf"),full_name:str=Form(),gender:str=Form(""),
    born_on:date|None=Form(None),birthplace:str=Form(""),phone:str=Form(""),address:str=Form(""),
    guardian_name:str=Form(""),guardian_relationship:str=Form(""),guardian_phone:str=Form(""),
    year_id:int=Form(),track_id:int=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator");assert_csrf(request,token)
    t=db.get(Track,track_id);y=db.get(Year,year_id)
    if not t or not y or t.year_id!=year_id or not center_allowed(user,t.center_id) or not full_name.strip():
        raise HTTPException(422,"السنة أو المركز أو المسلك أو الاسم غير صحيح")
    if born_on and born_on>date.today():raise HTTPException(422,"تاريخ الازدياد غير صحيح")
    if born_on and (date.today() - born_on).days < 18*365 and not guardian_phone.strip():
        raise HTTPException(422,"هاتف ولي الأمر إلزامي بالنسبة للقاصر")
    match=db.scalar(select(Learner).where(Learner.full_name==full_name.strip()[:180],Learner.born_on==born_on))
    if match: raise HTTPException(409,"توجد بطاقة مشابهة؛ راجعها لتجنب التكرار")
    l=Learner(full_name=full_name.strip()[:180],gender=gender[:20],born_on=born_on,birthplace=birthplace[:120],
      phone=phone[:45],address=address[:240],guardian_name=guardian_name[:180],
      guardian_relationship=guardian_relationship[:70],guardian_phone=guardian_phone[:45])
    db.add(l);db.flush()
    en=Enrollment(learner_id=l.id,year_id=year_id,center_id=t.center_id,track_id=t.id)
    db.add(en);db.flush();log(db,user,"enroll","enrollment",en.id);db.commit()
    return redirect(f"/platform/learners/{en.id}")

@app.get("/platform/learners/{enrollment_id}",response_class=HTMLResponse)
def learner_detail(enrollment_id:int,request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    en=learner_scope(db,user,enrollment_id);l=db.get(Learner,en.learner_id);csrf=request.session["csrf"]
    atts=db.execute(select(Attendance,SessionDay).join(SessionDay,Attendance.session_id==SessionDay.id)
                    .where(Attendance.enrollment_id==en.id).order_by(SessionDay.held_on.desc())).all()
    pending=sum(a.status=="absent" and a.permit_id is None for a,s in atts)
    historic=sum(a.status=="absent" for a,s in atts)
    days=len({s.held_on for a,s in atts if a.status=="absent"})
    state='<span class="danger">أحمر: غياب معلق</span>' if pending else '<span class="good">أخضر: الغياب مسوى</span>'
    if disciplinary_status(db,en):state+=' <span class="danger">· إقصاء تأديبي مستقل</span>'
    body=f'<section><h2>{e(l.full_name)}</h2><p>{e(db.get(Center,en.center_id).name)} · {e(db.get(Track,en.track_id).name)}</p><p>{state}</p><p>حصص الغياب: {historic} · أيام الغياب: {days} · غياب لم يشمله إذن دخول: {pending}</p></section>'
    if user["role"] in ("admin","coordinator"):
        body+=f'<section><h2>المعلومات العائلية (مقيدة)</h2><p>ولي الأمر: {e(l.guardian_name)} · هاتف: {e(l.guardian_phone)} · الصفة: {e(l.guardian_relationship)}</p></section>'
    arows=[[e(s.held_on),e(a.status),e("مغطى بإذن" if a.permit_id else "معلّق" if a.status=="absent" else "—")] for a,s in atts]
    body+=f'<section><h2>سجل الحضور والغياب</h2>{table(["التاريخ","الحالة","إذن الدخول"],arows)}</section>'
    if user["role"]=="coordinator" and pending:
        body+='<section><h2>إذن دخول معلل يغطي الغيابات المعلقة</h2>'+form(f"/platform/learners/{en.id}/permit",csrf,
          area("reason","التبرير الإلزامي"),"إصدار إذن الدخول")+'</section>'
    results=transcript(db,en)
    items=[[e(k),e(v) if v is not None else "غير مكتمل"] for k,v in results.items()]
    body+='<section><h2>المعدلات المعتمدة (من 20)</h2>'+table(["المادة / المعدل","النقطة"],items)+'</section>'
    return page(request,user,"ملف المستفيد",body)

@app.post("/platform/learners/{enrollment_id}/permit")
def grant_permit(enrollment_id:int,request:Request,token:str=Form(alias="_csrf"),reason:str=Form(),
                 db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);en=learner_scope(db,user,enrollment_id);coordinator(user,en.center_id)
    if len(reason.strip())<6:raise HTTPException(422,"يجب كتابة سبب واضح لإذن الدخول")
    pending=db.scalars(select(Attendance).where(Attendance.enrollment_id==en.id,Attendance.status=="absent",
                                                 Attendance.permit_id.is_(None))).all()
    if not pending:raise HTTPException(409,"لا توجد غيابات معلقة")
    p=Permit(learner_id=en.learner_id,center_id=en.center_id,reason=reason.strip()[:2000],issued_by=user["name"])
    db.add(p);db.flush()
    for a in pending:a.permit_id=p.id
    log(db,user,"issue_permit","permit",p.id,reason);db.commit()
    return redirect(f"/platform/learners/{en.id}")

def overlaps(first:Slot, second:Slot) -> bool:
    if first.weekday!=second.weekday:return False
    if first.specific_date and second.specific_date and first.specific_date!=second.specific_date:return False
    if first.specific_date and first.specific_date.weekday()!=second.weekday:return False
    if second.specific_date and second.specific_date.weekday()!=first.weekday:return False
    return first.starts_at < second.ends_at and second.starts_at < first.ends_at

def slot_conflict(db:Session,slot:Slot):
    query=select(Slot).where(Slot.staff_id==slot.staff_id,Slot.year_id==slot.year_id,
                            Slot.weekday==slot.weekday,Slot.canceled.is_(False))
    for other in db.scalars(query).all():
        if other.id!=slot.id and overlaps(other,slot):return other
    return None

@app.get("/platform/schedule",response_class=HTMLResponse)
def schedule_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    csrf=request.session["csrf"];ensure_data(db)
    slots=[s for s in db.scalars(select(Slot).order_by(Slot.weekday,Slot.starts_at)).all()
           if center_allowed(user,s.center_id) and (user["role"] in ("admin","coordinator") or s.staff_id==user["id"])]
    rows=[]
    for s in slots:
        c=db.get(Center,s.center_id);t=db.get(Track,s.track_id);staff=db.get(Staff,s.staff_id)
        action=form("/platform/sessions",csrf,field("slot_id","رقم الحصة",value=s.id)+field("held_on","التاريخ","date"),"فتح الحصة") if not s.canceled else "ملغاة"
        rows.append([e(c.name),e(t.name),e(s.subject),e("الإثنين الثلاثاء الأربعاء الخميس الجمعة السبت الأحد".split()[s.weekday]),
                     e(s.starts_at+"–"+s.ends_at),e(staff.full_name),action])
    body='<section><h2>الجدول الأسبوعي وحصص الاستثناء</h2>'+table(["المركز","المسلك","المادة","اليوم","الساعة","المؤطر","العمل"],rows)+'</section>'
    if user["role"]=="coordinator":
        staff=db.scalars(select(Staff).where(Staff.enabled.is_(True))).all()
        body=('<section><h2>برمجة حصة · منع التعارض آليًا عبر المركزين</h2>'+form("/platform/schedule",csrf,
          choose("track_id","مسلك المركز",tracks_options(db,user))+
          choose("staff_id","المؤطر",[(s.id,s.full_name) for s in staff if s.role in ("instructor","educator")])+
          choose("subject","المادة",[("مهني","التكوين المهني")]+[(s,s) for s in SUBJECTS])+
          choose("weekday","اليوم",list(enumerate("الإثنين الثلاثاء الأربعاء الخميس الجمعة السبت الأحد".split())))+
          field("starts_at","من","time")+field("ends_at","إلى","time")+
          field("specific_date","تاريخ استثنائي (اتركه فارغًا للجدول الأسبوعي)","date",False)+
          area("reason","مبرر البرمجة / التعديل"),"إضافة الحصة")+'</section><section><h2>عطلة أو إغلاق استثنائي</h2>'+
          form("/platform/closures",csrf,choose("center_id","المركز",center_options(db,user))+
          field("closed_on","اليوم المغلق","date")+area("reason","سبب الإغلاق"),"إلغاء حصص هذا اليوم")+'</section>'+body)
    return page(request,user,"الحصص والجداول",body)

@app.post("/platform/schedule")
def create_slot(request:Request,token:str=Form(alias="_csrf"),track_id:int=Form(),staff_id:int=Form(),
       subject:str=Form(),weekday:int=Form(),starts_at:str=Form(),ends_at:str=Form(),
       specific_date:date|None=Form(None),reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    track=db.get(Track,track_id);staff=db.get(Staff,staff_id)
    if not track or not staff:raise HTTPException(422,"المسلك أو المؤطر غير موجود")
    coordinator(user,track.center_id)
    if staff.role not in ("instructor","educator") or subject not in ("مهني",*SUBJECTS):raise HTTPException(422,"المادة غير صحيحة")
    if (staff.role=="instructor" and subject!="مهني") or (staff.role=="educator" and subject not in SUBJECTS):raise HTTPException(422,"عدم تطابق التخصص")
    if not any(a.center_id==track.center_id and a.track_id==track.id and a.subject==("" if subject=="مهني" else subject)
               for a in db.scalars(select(Assignment).where(Assignment.staff_id==staff_id)).all()):
        raise HTTPException(403,"هذا الموظف غير مكلف بهذا المسلك والمادة")
    if not 0<=weekday<=6 or not "00:00"<=starts_at<ends_at<="23:59" or (specific_date and specific_date.weekday()!=weekday):
        raise HTTPException(422,"اليوم أو التوقيت غير صحيح")
    if len(reason.strip())<5:raise HTTPException(422,"التبرير إلزامي لتغيير الجدولة")
    slot=Slot(year_id=track.year_id,center_id=track.center_id,track_id=track.id,staff_id=staff_id,
              subject=subject,kind="professional" if subject=="مهني" else "pedagogical",
              weekday=weekday,starts_at=starts_at,ends_at=ends_at,specific_date=specific_date,change_reason=reason.strip()[:1000])
    other=slot_conflict(db,slot)
    if other:raise HTTPException(409,f"تعارض: المؤطر مرتبط بالحصة رقم {other.id} في الوقت نفسه.")
    db.add(slot);db.flush();log(db,user,"schedule","slot",slot.id,reason)
    db.add(Notification(staff_id=staff_id,message=f"حصة جديدة: {subject}، يوم {weekday} الساعة {starts_at}",target="/platform/schedule"))
    db.commit();return redirect("/platform/schedule")

@app.post("/platform/closures")
def create_closure(request:Request,token:str=Form(alias="_csrf"),center_id:int=Form(),closed_on:date=Form(),
        reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);coordinator(user,center_id)
    if len(reason.strip())<6:raise HTTPException(422,"سبب الإغلاق إلزامي")
    active=db.scalars(select(SessionDay).join(Slot,SessionDay.slot_id==Slot.id).where(
        Slot.center_id==center_id,SessionDay.held_on==closed_on)).all()
    for session in active:
        if db.scalar(select(Attendance).where(Attendance.session_id==session.id)):
            raise HTTPException(409,"لا يمكن إلغاء حصة سُجل فيها حضور؛ يلزم معالجة السجل أولًا")
    db.add(Closure(center_id=center_id,closed_on=closed_on,reason=reason.strip()[:1000],actor=user["name"]))
    for session in active:session.status="canceled"
    try:log(db,user,"closure","center",center_id,reason);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,"الإغلاق مسجل سابقًا")
    return redirect("/platform/schedule")

@app.post("/platform/sessions")
def open_session(request:Request,token:str=Form(alias="_csrf"),slot_id:int=Form(),held_on:date=Form(),
                 db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    slot=slot_scope(db,user,slot_id)
    if slot.canceled or held_on.weekday()!=slot.weekday or (slot.specific_date and slot.specific_date!=held_on):
        raise HTTPException(422,"الحصة ملغاة أو التاريخ لا يطابق الجدول")
    if db.scalar(select(Closure).where(Closure.center_id==slot.center_id,Closure.closed_on==held_on)):
        raise HTTPException(409,"هذا اليوم مغلق؛ لا يُحتسب الغياب")
    item=db.scalar(select(SessionDay).where(SessionDay.slot_id==slot.id,SessionDay.held_on==held_on))
    if not item:
        if user["id"]!=slot.staff_id and user["role"] not in ("admin","coordinator"):
            raise HTTPException(403)
        item=SessionDay(slot_id=slot.id,held_on=held_on)
        db.add(item);db.flush();log(db,user,"open_session","session",item.id);db.commit()
    return redirect(f"/platform/sessions/{item.id}")

@app.get("/platform/sessions/{session_id}",response_class=HTMLResponse)
def session_page(session_id:int,request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    s=db.get(SessionDay,session_id)
    if not s:raise HTTPException(404)
    slot=slot_scope(db,user,s.slot_id)
    enrollments=db.scalars(select(Enrollment).where(Enrollment.year_id==slot.year_id,
       Enrollment.center_id==slot.center_id,Enrollment.track_id==slot.track_id,Enrollment.status=="active")).all()
    marks={a.enrollment_id:a for a in db.scalars(select(Attendance).where(Attendance.session_id==s.id)).all()}
    rows=[]
    for en in enrollments:
        l=db.get(Learner,en.learner_id)
        pend=db.scalar(select(func.count()).select_from(Attendance).where(Attendance.enrollment_id==en.id,
                    Attendance.status=="absent",Attendance.permit_id.is_(None))) or 0
        discipline=disciplinary_status(db,en,s.held_on)
        state='<span class="danger">إقصاء تأديبي</span>' if discipline else '<span class="danger">غياب سابق معلق</span>' if pend else '<span class="good">مسموح بالدخول</span>'
        old=marks.get(en.id)
        action="—"
        if user["id"]==slot.staff_id and s.status!="canceled" and not discipline:
            action=form(f"/platform/sessions/{s.id}/attendance",request.session["csrf"],
                f'<input type="hidden" name="enrollment_id" value="{en.id}">'+
                choose("status","الحضور",[("present","حاضر"),("absent","غائب"),("late","متأخر")],old.status if old else None),
                "تسجيل")
        rows.append([f'<a href="/platform/learners/{en.id}">{e(l.full_name)}</a>',state,
           e(old.status if old else "لم يسجل"),action])
    report=db.scalar(select(SessionReport).where(SessionReport.session_id==s.id))
    body=f'<section><h2>{e(slot.subject)} · {e(s.held_on)}</h2><p>الساعة {e(slot.starts_at)}–{e(slot.ends_at)} · الحالة: {e(s.status)}</p>{table(["المستفيد","التنبيه","الحضور","الإجراء"],rows)}</section>'
    if user["id"]==slot.staff_id and (not report or report.status in ("draft","returned")):
        body+='<section><h2>تقرير الحصة الواحدة</h2>'+form(f"/platform/sessions/{s.id}/report",request.session["csrf"],
            field("subject","عنوان التقرير",value=report.subject if report else slot.subject)+
            area("objectives","الأهداف")+area("activities","الأنشطة")+area("results","النتائج والمهارات المكتسبة")+
            area("challenges","الصعوبات")+area("recommendations","التوصيات"),"حفظ مسودة التقرير")+'</section>'
        if report:body+=f'<section>{form(f"/platform/reports/{report.id}/submit",request.session["csrf"],"","إرسال للمراجعة")}</section>'
    if report:
        body+=f'<section><p>حالة التقرير: {e(STATUS_LABELS.get(report.status,report.status))}</p><p>{e(report.review_comment)}</p><p><a class="button" href="/platform/reports/{report.id}/attachments">مرفقات التقرير</a></p></section>'
    return page(request,user,"تدبير الحصة",body)

def absence_notice(db:Session,en:Enrollment,held_on:date):
    atts=db.execute(select(Attendance,SessionDay).join(SessionDay,Attendance.session_id==SessionDay.id)
                    .where(Attendance.enrollment_id==en.id,Attendance.status=="absent")).all()
    monday=held_on-timedelta(days=held_on.weekday())
    first=held_on.replace(day=1)
    from platform_v2_models import Setting
    current={x.key:x.value for x in db.scalars(select(Setting).where(
        Setting.key.in_(("weekly_sessions","weekly_days","monthly_sessions","monthly_days")))).all()}
    for label,cut,limit_sessions,limit_days in (
         ("weekly",monday,current.get("weekly_sessions",3),current.get("weekly_days",2)),
         ("monthly",first,current.get("monthly_sessions",8),current.get("monthly_days",5))):
        if label=="weekly":matched=[s for a,s in atts if monday<=s.held_on<=monday+timedelta(days=6)]
        else:matched=[s for a,s in atts if first<=s.held_on and s.held_on.year==first.year and s.held_on.month==first.month]
        if len(matched)<limit_sessions and len({s.held_on for s in matched})<limit_days:continue
        period=f"{label}:{cut.isoformat()}"
        exists=db.scalar(select(Notice).where(Notice.enrollment_id==en.id,Notice.kind=="absence",Notice.period_key==period))
        if not exists:
            text=f"إشعار بخصوص الغياب المتكرر: {len(matched)} حصص في {len(set(s.held_on for s in matched))} أيام خلال {label}."
            n=Notice(enrollment_id=en.id,kind="absence",period_key=period,body=text,status="draft")
            db.add(n);db.flush();notify(db,en.center_id,f"مسودة إشعار غياب متكرر للمستفيد رقم {en.learner_id}",f"/platform/notices")

@app.post("/platform/sessions/{session_id}/attendance")
def mark_attendance(session_id:int,request:Request,token:str=Form(alias="_csrf"),
                    enrollment_id:int=Form(),status:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    s=db.get(SessionDay,session_id)
    if not s:raise HTTPException(404)
    slot=slot_scope(db,user,s.slot_id,for_write=True)
    en=learner_scope(db,user,enrollment_id)
    if en.center_id!=slot.center_id or en.track_id!=slot.track_id or en.year_id!=slot.year_id:
        raise HTTPException(403,"المستفيد غير مسجل في هذه الحصة")
    if status not in ("present","absent","late") or s.status=="canceled" or disciplinary_status(db,en,s.held_on):
        raise HTTPException(422,"لا يجوز تسجيل الغياب لحصة ملغاة أو أثناء الإقصاء التأديبي")
    a=db.scalar(select(Attendance).where(Attendance.session_id==s.id,Attendance.enrollment_id==en.id))
    if not a:a=Attendance(session_id=s.id,enrollment_id=en.id,status=status,recorded_by=user["name"]);db.add(a)
    else:
        if a.status=="absent" and a.permit_id and status!="absent":
            raise HTTPException(409,"تعديل غياب تمت تسويته يتطلب مراجعة المنسق")
        a.status=status
        if status=="absent":a.permit_id=None
    db.flush()
    if status=="absent":absence_notice(db,en,s.held_on)
    log(db,user,"attendance","session",s.id);db.commit()
    return redirect(f"/platform/sessions/{s.id}")


from platform_v2_models import GradeCorrection, CenterReport, Attachment

@app.post("/platform/sessions/{session_id}/report")
def save_report(session_id:int,request:Request,token:str=Form(alias="_csrf"),subject:str=Form(),
        objectives:str=Form(""),activities:str=Form(""),results:str=Form(""),
        challenges:str=Form(""),recommendations:str=Form(""),
        db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    s=db.get(SessionDay,session_id)
    if not s or s.status=="canceled":raise HTTPException(404)
    slot=slot_scope(db,user,s.slot_id,for_write=True)
    report=db.scalar(select(SessionReport).where(SessionReport.session_id==s.id))
    if report and report.status not in ("draft","returned"):
        raise HTTPException(409,"التقرير المرسل أو المعتمد مقفل")
    if not subject.strip():raise HTTPException(422,"عنوان التقرير إلزامي")
    if not report:report=SessionReport(session_id=s.id,author_id=user["id"],subject=subject.strip()[:200]);db.add(report)
    for key,val in dict(subject=subject,objectives=objectives,activities=activities,results=results,
            challenges=challenges,recommendations=recommendations).items():
        setattr(report,key,val.strip()[:10000])
    report.status="draft";report.review_comment=""
    db.flush();log(db,user,"save","session_report",report.id);db.commit()
    return redirect(f"/platform/sessions/{s.id}")

@app.post("/platform/reports/{report_id}/submit")
def submit_report(report_id:int,request:Request,token:str=Form(alias="_csrf"),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    r=db.get(SessionReport,report_id)
    if not r:raise HTTPException(404)
    s=db.get(SessionDay,r.session_id)
    slot=slot_scope(db,user,s.slot_id,for_write=True)
    if r.author_id!=user["id"] or r.status not in ("draft","returned"):
        raise HTTPException(403,"يمكن إرسال المسودات الخاصة بك فقط")
    r.status="submitted";db.flush();log(db,user,"submit","session_report",r.id)
    notify(db,slot.center_id,"تقرير حصة ينتظر المراجعة",f"/platform/reports")
    db.commit();return redirect("/platform/reports")

@app.get("/platform/reports",response_class=HTMLResponse)
def review_reports(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    csrf=request.session["csrf"]
    reports=db.scalars(select(SessionReport).order_by(SessionReport.id.desc()).limit(600)).all()
    rows=[]
    for r in reports:
        s=db.get(SessionDay,r.session_id);slot=db.get(Slot,s.slot_id)
        if not center_allowed(user,slot.center_id):continue
        if user["role"] in ("instructor","educator") and r.author_id!=user["id"]:continue
        approve=""
        if user["role"]=="coordinator" and r.status=="submitted":
            approve=form(f"/platform/reports/{r.id}/review",csrf,'<input type="hidden" name="decision" value="approved">',"مصادقة") + form(
                f"/platform/reports/{r.id}/review",csrf,'<input type="hidden" name="decision" value="returned">'+area("comment","سبب الإرجاع"),"إرجاع للتصحيح")
        approve+=f' <a class="button" href="/platform/reports/{r.id}/attachments">المرفقات</a>'
        rows.append([e(s.held_on),e(slot.subject),e(r.subject),e(STATUS_LABELS.get(r.status,r.status)),
                     e(r.review_comment),approve])
    body='<section><h2>تقارير الحصص المنفصلة</h2>'+table(["التاريخ","المادة","عنوان التقرير","الحالة","ملاحظة المنسق","المراجعة"],rows)+'</section>'
    if user["role"] in ("admin","coordinator"):
        summaries=db.scalars(select(CenterReport).order_by(CenterReport.id.desc()).limit(200)).all()
        summaryrows=[]
        for item in summaries:
            if not center_allowed(user,item.center_id):continue
            action=""
            if user["role"]=="coordinator" and item.author_id==user["id"] and item.status in ("draft","returned"):
                action=form(f"/platform/summary/{item.id}/submit",csrf,"","إرسال للإدارة")
            if user["role"]=="admin" and item.status=="submitted":
                action=form(f"/platform/summary/{item.id}/review",csrf,
                            '<input name="decision" type="hidden" value="approved">',"مصادقة")
                action+=form(f"/platform/summary/{item.id}/review",csrf,
                            '<input name="decision" type="hidden" value="returned">'+area("comment","سبب الإرجاع"),"إرجاع")
            summaryrows.append([e(db.get(Center,item.center_id).name),e(item.period_type),e(item.period_start),
                                e(item.period_end),e(STATUS_LABELS.get(item.status,item.status)),action])
        body+='<section><h2>تقارير المنسقين (المصادقة من إدارة الجمعية)</h2>'+table(["المركز","المدة","من","إلى","الحالة","الإجراء"],summaryrows)+'</section>'
        if user["role"]=="coordinator":
            years=db.scalars(select(Year)).all()
            body+='<section><h2>تقرير دوري للمنسق</h2>'+form("/platform/summary",csrf,
                choose("year_id","السنة",[(y.id,y.label) for y in years])+
                choose("center_id","المركز",center_options(db,user))+
                choose("period_type","التواتر",[("weekly","أسبوعي"),("monthly","شهري"),("activity","نشاط"),("annual","سنوي")])+
                field("period_start","من","date")+field("period_end","إلى","date")+area("analysis","التحليل والتوصيات"),"حفظ التقرير")+'</section>'
    return page(request,user,"التقارير والمصادقة",body)

@app.post("/platform/reports/{report_id}/review")
def review_report(report_id:int,request:Request,token:str=Form(alias="_csrf"),decision:str=Form(),comment:str=Form(""),
                  db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    r=db.get(SessionReport,report_id)
    if not r:raise HTTPException(404)
    slot=db.get(Slot,db.get(SessionDay,r.session_id).slot_id)
    coordinator(user,slot.center_id)
    if r.status!="submitted" or decision not in ("approved","returned"):
        raise HTTPException(409,"حالة المراجعة غير صحيحة")
    if decision=="returned" and len(comment.strip())<5:raise HTTPException(422,"سبب الإرجاع إلزامي")
    r.status=decision;r.review_comment=comment.strip()[:1500];r.reviewed_by=user["name"];r.reviewed_at=now()
    db.add(Notification(staff_id=r.author_id,message=f"تحديث تقرير الحصة: {STATUS_LABELS[decision]}",target=f"/platform/sessions/{r.session_id}"))
    log(db,user,decision,"session_report",r.id,comment);db.commit()
    return redirect("/platform/reports")

@app.post("/platform/summary")
def create_center_summary(request:Request,token:str=Form(alias="_csrf"),year_id:int=Form(),center_id:int=Form(),
          period_type:str=Form(),period_start:date=Form(),period_end:date=Form(),analysis:str=Form(""),
          db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);coordinator(user,center_id)
    if period_start>period_end or period_type not in ("weekly","monthly","activity","annual") or not db.get(Year,year_id):
        raise HTTPException(422,"بيانات الفترة غير صحيحة")
    r=CenterReport(year_id=year_id,center_id=center_id,period_type=period_type,
            period_start=period_start,period_end=period_end,analysis=analysis[:12000],author_id=user["id"])
    db.add(r);db.flush();log(db,user,"create","center_report",r.id);db.commit()
    return redirect("/platform/reports")

@app.post("/platform/summary/{report_id}/submit")
def submit_center_summary(report_id:int,request:Request,token:str=Form(alias="_csrf"),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);r=db.get(CenterReport,report_id)
    if not r:raise HTTPException(404)
    coordinator(user,r.center_id)
    if r.author_id!=user["id"] or r.status not in ("draft","returned"):raise HTTPException(403)
    r.status="submitted";log(db,user,"submit","center_report",r.id);db.commit()
    return redirect("/platform/reports")

@app.post("/platform/summary/{report_id}/review")
def review_center_summary(report_id:int,request:Request,token:str=Form(alias="_csrf"),
        decision:str=Form(),comment:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    r=db.get(CenterReport,report_id)
    if not r:raise HTTPException(404)
    if r.status!="submitted" or decision not in ("approved","returned"):raise HTTPException(409)
    if decision=="returned" and len(comment.strip())<5:raise HTTPException(422,"سبب الإرجاع إلزامي")
    r.status=decision;r.reviewed_by=user["name"];r.reviewed_at=now();r.review_comment=comment.strip()[:2000]
    db.add(Notification(staff_id=r.author_id,message=f"تمت مراجعة التقرير الدوري: {STATUS_LABELS[decision]}",target="/platform/reports"))
    log(db,user,decision,"center_report",r.id,comment);db.commit()
    return redirect("/platform/reports")

def score_value(value:str)->Decimal:
    try:x=Decimal(value)
    except InvalidOperation:raise HTTPException(422,"النقطة غير رقمية")
    if not x.is_finite() or x<0 or x>20 or x.as_tuple().exponent < -2:
        raise HTTPException(422,"النقط من 0 إلى 20 وبحد أقصى خانتين عشريتين")
    return x

def transcript(db:Session,en:Enrollment,start:date|None=None,end:date|None=None):
    assessments=db.scalars(select(Assessment).where(Assessment.track_id==en.track_id)).all()
    weighted={}
    for a in assessments:
        if start and a.held_on<start:continue
        if end and a.held_on>end:continue
        grade=db.scalar(select(Grade).where(Grade.assessment_id==a.id,Grade.enrollment_id==en.id,Grade.status=="approved"))
        if not grade:continue
        weighted.setdefault(a.subject,[]).append((grade.score,a.coefficient))
    def average(points):
        if not points:return None
        denom=sum((c for _,c in points),Decimal(0))
        if denom<=0:return None
        return (sum((score*c for score,c in points),Decimal(0))/denom).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    output={"المعدل المهني":average(weighted.get("مهني",[]))}
    for subject in SUBJECTS:output[subject]=average(weighted.get(subject,[]))
    output["معدل التأهيل التربوي"]=(
        (sum((output[s] for s in SUBJECTS),Decimal(0))/Decimal(3)).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        if all(output[s] is not None for s in SUBJECTS) else None)
    return output

@app.get("/platform/grades",response_class=HTMLResponse)
def grades_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    csrf=request.session["csrf"]
    assessments=[a for a in db.scalars(select(Assessment).order_by(Assessment.held_on.desc()).limit(500)).all()
                 if any(t.id==a.track_id for t in db.scalars(select(Track)).all() if center_allowed(user,t.center_id))
                 and (user["role"] in ("admin","coordinator") or a.staff_id==user["id"])]
    rows=[]
    for a in assessments:
        t=db.get(Track,a.track_id)
        grades=db.scalars(select(Grade).where(Grade.assessment_id==a.id)).all()
        related=[]
        for g in grades:
            en=db.get(Enrollment,g.enrollment_id);l=db.get(Learner,en.learner_id)
            action=""
            if user["role"]=="coordinator" and center_allowed(user,t.center_id) and g.status=="pending":
                action=form(f"/platform/grades/{g.id}/approve",csrf,"","مصادقة")
            if user["id"]==a.staff_id and g.status=="approved":
                action+=form(f"/platform/grades/{g.id}/correction",csrf,
                    field("new_score","النقطة الجديدة","number",extra='min="0" max="20" step="0.01"')+
                    area("reason","سبب التصحيح"),"طلب تصحيح")
            related.append(f"{e(l.full_name)}: {e(g.score)} ({e(g.status)}) {action}")
        rows.append([e(db.get(Track,a.track_id).name),e(a.subject),e(a.title),e(a.held_on),e(a.coefficient),
            "<hr>".join(related)])
    body='<section><h2>الاختبارات والنقط حسب المادة</h2>'+table(["المسلك","المادة","الاختبار","التاريخ","المعامل","النقط"],rows)+'</section>'
    if user["role"] in ("instructor","educator"):
        allowed=[]
        for asgn in user["assignments"]:
            if asgn.track_id:
                track=db.get(Track,asgn.track_id)
                if track:allowed.append((track.id,f"{track.name} · {asgn.subject or 'مهني'}"))
        body='<section><h2>إنشاء تقييم بمعامل مستقل</h2>'+form("/platform/assessments",csrf,
          choose("track_id","المسلك",allowed)+choose("subject","المادة",[("مهني","التكوين المهني")]+[(s,s) for s in SUBJECTS])+
          field("title","عنوان الاختبار")+field("held_on","تاريخ الاختبار","date")+
          field("coefficient","المعامل","number",extra='step="0.01" min="0.01"'),"إنشاء الاختبار")+'</section>'+body
        body+='<section><h2>إدخال نقطة</h2>'+form("/platform/grades",csrf,
           choose("assessment_id","الاختبار",[(a.id,f"{a.title} · {a.subject}") for a in assessments if a.staff_id==user["id"]])+
           field("enrollment_id","رقم تسجيل المستفيد","number",extra='min="1"')+
           field("score","النقطة من 20","number",extra='min="0" max="20" step="0.01"'),"حفظ نقطة للمراجعة")+'</section>'
    if user["role"]=="coordinator":
        pending=db.scalars(select(GradeCorrection).where(GradeCorrection.status=="pending")).all()
        cr=[]
        for item in pending:
            g=db.get(Grade,item.grade_id);a=db.get(Assessment,g.assessment_id)
            track=db.get(Track,a.track_id)
            if not center_allowed(user,track.center_id):continue
            cr.append([e(g.id),e(g.score),e(item.new_score),e(item.reason),
                form(f"/platform/corrections/{item.id}/review",csrf,'<input type="hidden" name="decision" value="approved">',"قبول")+
                form(f"/platform/corrections/{item.id}/review",csrf,'<input type="hidden" name="decision" value="rejected">',"رفض")])
        body+='<section><h2>طلبات تصحيح النقط المعتمدة</h2>'+table(["النقطة","قبل","بعد","التعليل","القرار"],cr)+'</section>'
    return page(request,user,"النقط والتقييم",body)

@app.post("/platform/assessments")
def create_assessment(request:Request,token:str=Form(alias="_csrf"),track_id:int=Form(),subject:str=Form(),
     title:str=Form(),held_on:date=Form(),coefficient:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"instructor","educator");assert_csrf(request,token)
    t=db.get(Track,track_id)
    if not t or not any(a.track_id==t.id and a.subject==("" if subject=="مهني" else subject) for a in user["assignments"]):
        raise HTTPException(403,"المسلك أو المادة غير مسندين إليك")
    if subject not in ("مهني",*SUBJECTS) or not title.strip():raise HTTPException(422,"بيانات الاختبار غير صحيحة")
    try:coeff=Decimal(coefficient)
    except InvalidOperation:raise HTTPException(422,"المعامل غير صحيح")
    if not coeff.is_finite() or coeff<=0 or coeff>100:raise HTTPException(422,"المعامل يجب أن يكون موجبًا")
    a=Assessment(track_id=t.id,staff_id=user["id"],subject=subject,title=title.strip()[:180],held_on=held_on,coefficient=coeff)
    db.add(a);db.flush();log(db,user,"create","assessment",a.id);db.commit();return redirect("/platform/grades")

@app.post("/platform/grades")
def grade_save(request:Request,token:str=Form(alias="_csrf"),assessment_id:int=Form(),enrollment_id:int=Form(),
     score:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"instructor","educator");assert_csrf(request,token)
    a=db.get(Assessment,assessment_id);en=learner_scope(db,user,enrollment_id)
    if not a or a.staff_id!=user["id"] or a.track_id!=en.track_id:raise HTTPException(403,"الاختبار أو التسجيل غير مصرح به")
    value=score_value(score)
    g=db.scalar(select(Grade).where(Grade.assessment_id==assessment_id,Grade.enrollment_id==en.id))
    if g and g.status=="approved":raise HTTPException(409,"النقطة المعتمدة لا تُعدل إلا بطلب تصحيح معلل")
    if not g:g=Grade(assessment_id=a.id,enrollment_id=en.id,score=value);db.add(g)
    else:g.score=value
    db.flush();log(db,user,"save","grade",g.id);notify(db,en.center_id,"نقطة تنتظر المصادقة","/platform/grades")
    db.commit();return redirect("/platform/grades")

@app.post("/platform/grades/{grade_id}/approve")
def approve_grade(grade_id:int,request:Request,token:str=Form(alias="_csrf"),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    g=db.get(Grade,grade_id)
    if not g:raise HTTPException(404)
    en=db.get(Enrollment,g.enrollment_id)
    coordinator(user,en.center_id)
    if g.status!="pending":raise HTTPException(409,"النقطة غير معلقة")
    g.status="approved";g.approved_by=user["name"]
    log(db,user,"approve","grade",g.id);db.commit();return redirect("/platform/grades")

@app.post("/platform/grades/{grade_id}/correction")
def request_correction(grade_id:int,request:Request,token:str=Form(alias="_csrf"),new_score:str=Form(),
        reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);g=db.get(Grade,grade_id)
    if not g or g.status!="approved":raise HTTPException(409,"النقطة غير معتمدة")
    a=db.get(Assessment,g.assessment_id)
    if user["id"]!=a.staff_id:raise HTTPException(403)
    if len(reason.strip())<6:raise HTTPException(422,"سبب التصحيح إلزامي")
    if db.scalar(select(GradeCorrection).where(GradeCorrection.grade_id==g.id,GradeCorrection.status=="pending")):
        raise HTTPException(409,"يوجد طلب تصحيح معلق")
    change=GradeCorrection(grade_id=g.id,new_score=score_value(new_score),reason=reason.strip()[:2000],requested_by=user["name"])
    db.add(change);db.flush()
    en=db.get(Enrollment,g.enrollment_id)
    notify(db,en.center_id,"طلب تصحيح نقطة معتمدة","/platform/grades")
    log(db,user,"correction_request","grade",g.id,reason);db.commit();return redirect("/platform/grades")

@app.post("/platform/corrections/{change_id}/review")
def review_correction(change_id:int,request:Request,token:str=Form(alias="_csrf"),decision:str=Form(),
        db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);change=db.get(GradeCorrection,change_id)
    if not change or change.status!="pending" or decision not in ("approved","rejected"):raise HTTPException(409)
    g=db.get(Grade,change.grade_id)
    coordinator(user,db.get(Enrollment,g.enrollment_id).center_id)
    if decision=="approved":
        old=g.score;g.score=change.new_score
        log(db,user,"approve_correction","grade",g.id,f"old={old};new={g.score};reason={change.reason}")
    change.status=decision;change.reviewed_by=user["name"]
    db.commit();return redirect("/platform/grades")


@app.get("/platform/discipline",response_class=HTMLResponse)
def discipline_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    csrf=request.session["csrf"]
    incidents=db.scalars(select(Incident).order_by(Incident.id.desc()).limit(400)).all()
    irows=[]
    for inc in incidents:
        en=db.get(Enrollment,inc.enrollment_id)
        if not en or not center_allowed(user,en.center_id):continue
        if user["role"] in ("instructor","educator") and inc.staff_id!=user["id"]:continue
        action=""
        if user["role"]=="coordinator" and inc.status=="pending":
            action=form(f"/platform/incidents/{inc.id}/review",csrf,
                    '<input type="hidden" name="decision" value="approved">',"اعتماد")
            action+=form(f"/platform/incidents/{inc.id}/review",csrf,
                    '<input type="hidden" name="decision" value="rejected">'+area("comment","سبب الرفض"),"رفض")
        irows.append([e(db.get(Learner,en.learner_id).full_name),e(inc.kind),e(inc.description),
                      e(inc.educational_action),e(inc.status),action])
    body='<section><h2>الملاحظات السلوكية والإجراءات التربوية</h2>'+table(["المستفيد","النوع","التفاصيل","إجراء تربوي","الحالة","مراجعة المنسق"],irows)+'</section>'
    if user["role"] in ("instructor","educator"):
        body='<section><h2>تسجيل سلوك إيجابي أو سلبي (يتطلب مصادقة)</h2>'+form("/platform/incidents",csrf,
            field("enrollment_id","رقم تسجيل المستفيد","number",extra='min="1"')+
            choose("kind","النوع",[("positive","سلوك إيجابي"),("negative","سلوك سلبي")])+
            area("description","وصف الواقعة")+area("educational_action","الإجراء التربوي البسيط"),"إرسال للمراجعة")+'</section>'+body
    decisions=db.scalars(select(Discipline).order_by(Discipline.id.desc()).limit(400)).all()
    drows=[]
    for d in decisions:
        en=db.get(Enrollment,d.enrollment_id)
        if not en or not center_allowed(user,en.center_id):continue
        if user["role"] in ("instructor","educator"):continue
        action=""
        if user["role"]=="admin" and d.status=="pending_admin":
            action=form(f"/platform/discipline/{d.id}/approve",csrf,
                '<input type="hidden" name="decision" value="effective">',"مصادقة إدارة الجمعية")
            action+=form(f"/platform/discipline/{d.id}/approve",csrf,
                '<input type="hidden" name="decision" value="rejected">'+area("reason","سبب الرفض"),"رفض")
        if user["role"]=="coordinator" and d.status=="effective":
            action+=form(f"/platform/discipline/{d.id}/early-request",csrf,
                        area("reason","سبب طلب الرفع المبكر"),"طلب رفع مبكر")
            if d.excluded_through and date.today()>d.excluded_through and not d.return_authorized:
                action+=form(f"/platform/discipline/{d.id}/return",csrf,
                           area("reason","مبرر السماح بالعودة"),"ترخيص العودة بعد انتهاء المدة")
        if user["role"]=="admin" and d.status=="early_pending":
            action+=form(f"/platform/discipline/{d.id}/early-review",csrf,
                '<input type="hidden" name="decision" value="approved">'+area("reason","تعليل قرار الإدارة"),"قبول الرفع")
            action+=form(f"/platform/discipline/{d.id}/early-review",csrf,
                '<input type="hidden" name="decision" value="rejected">'+area("reason","تعليل الرفض"),"رفض الرفع")
        drows.append([e(db.get(Learner,en.learner_id).full_name),e(d.decision),e(d.minutes),
                      e(f"{d.excluded_from} — {d.excluded_through}"),e(d.status),action])
    body+='<section><h2>قرارات المجلس التأديبي (غير نافذة قبل اعتماد الإدارة)</h2>'+table(["المستفيد","القرار","المحضر","مدة الإقصاء","الحالة","الإجراء"],drows)+'</section>'
    if user["role"]=="coordinator":
        body='<section><h2>إحالة قرار مجلس تأديبي لإدارة الجمعية</h2>'+form("/platform/discipline",csrf,
             field("enrollment_id","رقم تسجيل المستفيد","number",extra='min="1"')+
             area("decision","التوصية / القرار")+area("minutes","محضر المجلس وأسبابه")+
             field("excluded_from","بداية الإقصاء إن وجد","date",False)+
             field("excluded_through","نهاية الإقصاء إن وجدت","date",False),"إرسال للمصادقة")+'</section>'+body
    return page(request,user,"السلوك والانضباط",body)

@app.post("/platform/incidents")
def record_incident(request:Request,token:str=Form(alias="_csrf"),enrollment_id:int=Form(),
       kind:str=Form(),description:str=Form(),educational_action:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"instructor","educator");assert_csrf(request,token)
    en=learner_scope(db,user,enrollment_id)
    if kind not in ("positive","negative") or len(description.strip())<5:raise HTTPException(422,"الوصف أو النوع غير صحيح")
    inc=Incident(enrollment_id=en.id,staff_id=user["id"],kind=kind,description=description.strip()[:4000],
                 educational_action=educational_action.strip()[:1200])
    db.add(inc);db.flush()
    notify(db,en.center_id,"ملاحظة سلوكية تنتظر المصادقة","/platform/discipline")
    log(db,user,"incident","incident",inc.id);db.commit();return redirect("/platform/discipline")

@app.post("/platform/incidents/{incident_id}/review")
def review_incident(incident_id:int,request:Request,token:str=Form(alias="_csrf"),decision:str=Form(),
         comment:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);inc=db.get(Incident,incident_id)
    if not inc or inc.status!="pending" or decision not in ("approved","rejected"):raise HTTPException(409)
    en=db.get(Enrollment,inc.enrollment_id);coordinator(user,en.center_id)
    if decision=="rejected" and len(comment.strip())<5:raise HTTPException(422,"سبب الرفض إلزامي")
    inc.status=decision;inc.review_comment=comment.strip()[:2000]
    db.add(Notification(staff_id=inc.staff_id,message=f"مراجعة الملاحظة السلوكية: {decision}",target="/platform/discipline"))
    log(db,user,decision,"incident",inc.id,comment);db.commit();return redirect("/platform/discipline")

@app.post("/platform/discipline")
def new_discipline(request:Request,token:str=Form(alias="_csrf"),enrollment_id:int=Form(),
         decision:str=Form(),minutes:str=Form(),excluded_from:date|None=Form(None),
         excluded_through:date|None=Form(None),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);en=learner_scope(db,user,enrollment_id);coordinator(user,en.center_id)
    if len(decision.strip())<5 or len(minutes.strip())<10:
        raise HTTPException(422,"القرار والمحضر المعلل إلزاميان")
    if (excluded_from is None)!=(excluded_through is None) or (excluded_from and excluded_from>excluded_through):
        raise HTTPException(422,"حددا مدة الإقصاء معًا أو اتركاها فارغة")
    d=Discipline(enrollment_id=en.id,decision=decision.strip()[:4000],minutes=minutes.strip()[:8000],
        excluded_from=excluded_from,excluded_through=excluded_through,status="pending_admin")
    db.add(d);db.flush();log(db,user,"council_submission","discipline",d.id);db.commit()
    return redirect("/platform/discipline")

@app.post("/platform/discipline/{discipline_id}/approve")
def admin_discipline(discipline_id:int,request:Request,token:str=Form(alias="_csrf"),
        decision:str=Form(),reason:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    d=db.get(Discipline,discipline_id)
    if not d or d.status!="pending_admin" or decision not in ("effective","rejected"):raise HTTPException(409)
    if decision=="rejected" and len(reason.strip())<5:raise HTTPException(422,"سبب الرفض إلزامي")
    d.status=decision;d.approved_by=user["name"]
    en=db.get(Enrollment,d.enrollment_id)
    if decision=="effective":
        n=Notice(enrollment_id=en.id,kind="discipline",body=d.decision,status="draft")
        db.add(n)
        notify(db,en.center_id,"قرار تأديبي نافذ ينتظر إشعارًا ورقيًا","/platform/notices")
    log(db,user,decision,"discipline",d.id,reason);db.commit()
    return redirect("/platform/discipline")

@app.post("/platform/discipline/{discipline_id}/early-request")
def early_request(discipline_id:int,request:Request,token:str=Form(alias="_csrf"),reason:str=Form(),
          db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);d=db.get(Discipline,discipline_id)
    if not d:raise HTTPException(404)
    en=db.get(Enrollment,d.enrollment_id);coordinator(user,en.center_id)
    if d.status!="effective" or not d.excluded_through or date.today()>d.excluded_through:
        raise HTTPException(409,"طلب الرفع المبكر غير متاح")
    if len(reason.strip())<6:raise HTTPException(422,"التعليل إلزامي")
    d.status="early_pending";d.early_lift_reason=reason.strip()[:2000]
    log(db,user,"early_lift_request","discipline",d.id,reason);db.commit()
    return redirect("/platform/discipline")

@app.post("/platform/discipline/{discipline_id}/early-review")
def early_review(discipline_id:int,request:Request,token:str=Form(alias="_csrf"),
        decision:str=Form(),reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    d=db.get(Discipline,discipline_id)
    if not d or d.status!="early_pending" or decision not in ("approved","rejected"):raise HTTPException(409)
    if len(reason.strip())<6:raise HTTPException(422,"التعليل إلزامي")
    if decision=="approved":
        d.status="lifted";d.return_authorized=True
    else:d.status="effective"
    log(db,user,"early_lift_"+decision,"discipline",d.id,reason);db.commit()
    return redirect("/platform/discipline")

@app.post("/platform/discipline/{discipline_id}/return")
def return_after_exclusion(discipline_id:int,request:Request,token:str=Form(alias="_csrf"),
       reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);d=db.get(Discipline,discipline_id)
    if not d:raise HTTPException(404)
    en=db.get(Enrollment,d.enrollment_id);coordinator(user,en.center_id)
    if d.status!="effective" or not d.excluded_through or date.today()<=d.excluded_through:
        raise HTTPException(409,"الإقصاء لم ينته بعد")
    if len(reason.strip())<6:raise HTTPException(422,"ترخيص العودة يتطلب تعليلًا")
    d.return_authorized=True;d.status="completed"
    log(db,user,"return_authorization","discipline",d.id,reason);db.commit()
    return redirect("/platform/discipline")

@app.get("/platform/notices",response_class=HTMLResponse)
def notices_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator")
    csrf=request.session["csrf"]
    notices=db.scalars(select(Notice).order_by(Notice.id.desc()).limit(500)).all()
    rows=[]
    for n in notices:
        en=db.get(Enrollment,n.enrollment_id)
        if not en or not center_allowed(user,en.center_id):continue
        action=""
        if user["role"]=="coordinator":
            if n.status=="draft":
                action+=form(f"/platform/notices/{n.id}/issue",csrf,area("body","صياغة الإشعار المعتمدة"),"اعتماد الإشعار وإصداره")
                action+=form(f"/platform/notices/{n.id}/defer",csrf,area("reason","سبب التأجيل"),"تأجيل")
            if n.status=="issued":
                action+=form(f"/platform/notices/{n.id}/deliver",csrf,
                             field("delivered_on","تاريخ التسليم","date",value=date.today()),"تم التسليم")
        if n.status in ("issued","delivered"):
            action+=f' <a class="button" href="/platform/notices/{n.id}/print" target="_blank">طباعة</a>'
        rows.append([e(db.get(Learner,en.learner_id).full_name),e(n.kind),e(n.body),
              e(STATUS_LABELS.get(n.status,n.status)),e(n.delivered_at or "—"),action])
    body=('<section><h2>المسودات التلقائية لا تعني إصدارًا تلقائيًا</h2><p class="subtle">لا تُرسل الإشعارات إلكترونيًا، وللمنسق وحده اعتماد نص الورقة وإصدارها ثم تسجيل حالة التسليم وتاريخه فقط.</p>'
          +table(["المستفيد","النوع","المضمون","الحالة","تاريخ التسليم","الإجراء"],rows)+'</section>')
    if user["role"]=="coordinator":
        body='<section><h2>إشعار يدوي</h2>'+form("/platform/notices",csrf,
         field("enrollment_id","رقم تسجيل المستفيد","number",extra='min="1"')+
         choose("kind","نوع الإشعار",[("absence","غياب"),("summons","استدعاء"),("discipline","قرار تأديبي"),("other","أخرى")])+
         area("body","مضمون الإشعار"),"إنشاء مسودة")+'</section>'+body
    return page(request,user,"الإشعارات الورقية",body)

@app.post("/platform/notices")
def manual_notice(request:Request,token:str=Form(alias="_csrf"),enrollment_id:int=Form(),
      kind:str=Form(),body:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);en=learner_scope(db,user,enrollment_id);coordinator(user,en.center_id)
    if kind not in ("absence","summons","discipline","other") or len(body.strip())<5:raise HTTPException(422)
    if kind=="discipline":
        approved=db.scalar(select(Discipline).where(Discipline.enrollment_id==en.id,
                           Discipline.status.in_(["effective","completed","lifted"])))
        if not approved:raise HTTPException(409,"لا يجوز إصدار إشعار قرار تأديبي قبل مصادقة إدارة الجمعية")
    n=Notice(enrollment_id=en.id,kind=kind,body=body.strip()[:4000])
    db.add(n);db.flush();log(db,user,"notice_draft","notice",n.id);db.commit()
    return redirect("/platform/notices")

@app.post("/platform/notices/{notice_id}/issue")
def issue_notice(notice_id:int,request:Request,token:str=Form(alias="_csrf"),body:str=Form(""),
      db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);n=db.get(Notice,notice_id)
    if not n:raise HTTPException(404)
    en=db.get(Enrollment,n.enrollment_id);coordinator(user,en.center_id)
    if n.status!="draft":raise HTTPException(409,"الإشعار غير قابل للإصدار")
    if body.strip():n.body=body.strip()[:4000]
    if len(n.body.strip())<5:raise HTTPException(422,"مضمون الإشعار إلزامي")
    n.status="issued";n.issued_at=now()
    log(db,user,"notice_issue","notice",n.id);db.commit();return redirect("/platform/notices")

@app.post("/platform/notices/{notice_id}/defer")
def defer_notice(notice_id:int,request:Request,token:str=Form(alias="_csrf"),reason:str=Form(),
     db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);n=db.get(Notice,notice_id)
    if not n:raise HTTPException(404)
    en=db.get(Enrollment,n.enrollment_id);coordinator(user,en.center_id)
    if n.status!="draft" or len(reason.strip())<5:raise HTTPException(422,"التعليل إلزامي")
    n.status="deferred";log(db,user,"notice_defer","notice",n.id,reason);db.commit();return redirect("/platform/notices")

@app.post("/platform/notices/{notice_id}/deliver")
def deliver_notice(notice_id:int,request:Request,token:str=Form(alias="_csrf"),delivered_on:date=Form(),
      db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token);n=db.get(Notice,notice_id)
    if not n:raise HTTPException(404)
    en=db.get(Enrollment,n.enrollment_id);coordinator(user,en.center_id)
    if n.status!="issued" or delivered_on>date.today() or (n.issued_at and delivered_on<n.issued_at.date()):
        raise HTTPException(422,"تاريخ التسليم أو حالة الإشعار غير صحيح")
    n.status="delivered";n.delivered_at=delivered_on
    log(db,user,"notice_delivery","notice",n.id);db.commit();return redirect("/platform/notices")

@app.get("/platform/notices/{notice_id}/print",response_class=HTMLResponse)
def print_notice(notice_id:int,request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator")
    n=db.get(Notice,notice_id)
    if not n:raise HTTPException(404)
    en=db.get(Enrollment,n.enrollment_id)
    if not center_allowed(user,en.center_id):raise HTTPException(403)
    if n.status not in ("issued","delivered"):raise HTTPException(409,"لا تطبع المسودة قبل اعتمادها")
    name=db.get(Learner,en.learner_id).full_name
    center=db.get(Center,en.center_id).name
    return HTMLResponse(f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>إشعار رسمي رقم {n.id}</title><style>body{{font-family:Tahoma,Arial,sans-serif;max-width:750px;
margin:45px auto;font-size:18px;line-height:2.1}}header{{text-align:center;border-bottom:2px solid #236ca3}}
main{{margin-top:40px}}footer{{display:flex;justify-content:space-between;margin-top:100px}}
@media print{{button{{display:none}}}}</style></head><body><header><h2>جمعية نور الأمل</h2>
<div>برنامج الفرصة الثانية – الجيل الجديد</div><p>{e(center)}</p></header>
<main><h3>إشعار رقم {n.id} · {e(n.kind)}</h3><p>المستفيد(ة): <strong>{e(name)}</strong></p>
<p>{e(n.body)}</p><p>تاريخ الإصدار: {e(n.issued_at.date() if n.issued_at else "")}</p></main>
<footer><div>توقيع منسق المركز: ........................</div><div>ختم الجمعية: ........................</div></footer>
<button onclick="window.print()">طباعة</button></body></html>''')

@app.get("/platform/notifications",response_class=HTMLResponse)
def notifications_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    if user["id"] is None:
        return page(request,user,"التنبيهات",'<section>المصادقات الإدارية تظهر ضمن صفحة التقارير والانضباط.</section>')
    rows=db.scalars(select(Notification).where(Notification.staff_id==user["id"]).order_by(Notification.id.desc()).limit(200)).all()
    csrf=request.session["csrf"]
    body=table(["التنبيه","التاريخ","الحالة","الإجراء"],[
        [f'<a href="{e(n.target)}">{e(n.message)}</a>',e(n.created_at),e("مقروء" if n.read else "جديد"),
         form(f"/platform/notifications/{n.id}/read",csrf,"","تمت القراءة") if not n.read else "—"] for n in rows])
    return page(request,user,"التنبيهات الداخلية",f"<section>{body}</section>")

@app.post("/platform/notifications/{notification_id}/read")
def mark_notification(notification_id:int,request:Request,token:str=Form(alias="_csrf"),
      db:Session=Depends(get_db),user=Depends(actor)):
    assert_csrf(request,token)
    n=db.get(Notification,notification_id)
    if not n or n.staff_id!=user["id"]:raise HTTPException(403)
    n.read=True;db.commit();return redirect("/platform/notifications")

@app.get("/platform/audit",response_class=HTMLResponse)
def audit_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin")
    rows=db.scalars(select(Audit).order_by(Audit.id.desc()).limit(400)).all()
    return page(request,user,"سجل العمليات",'<section>'+table(["التاريخ","المسؤول","العملية","المورد","معرف","التعليل"],[
        [e(a.created_at),e(a.actor),e(a.action),e(a.resource),e(a.resource_id),e(a.reason)] for a in rows])+'</section>')

from platform_v2_models import Setting

@app.get("/platform/settings",response_class=HTMLResponse)
def settings_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin")
    values={x.key:x.value for x in db.scalars(select(Setting)).all()}
    fields=field("weekly_sessions","عدد حصص الغياب الأسبوعية","number",value=values.get("weekly_sessions",3),extra='min="1" max="100"')+\
      field("weekly_days","عدد أيام الغياب الأسبوعية","number",value=values.get("weekly_days",2),extra='min="1" max="7"')+\
      field("monthly_sessions","عدد حصص الغياب الشهرية","number",value=values.get("monthly_sessions",8),extra='min="1" max="200"')+\
      field("monthly_days","عدد أيام الغياب الشهرية","number",value=values.get("monthly_days",5),extra='min="1" max="31"')+\
      area("reason","سبب التعديل")
    return page(request,user,"إعدادات التنبيه",'<section><h2>عتبات الغياب القابلة للتعديل مع تسجيل السبب</h2>'+
                form("/platform/settings",request.session["csrf"],fields,"حفظ العتبات")+'</section>')

@app.post("/platform/settings")
def update_settings(request:Request,token:str=Form(alias="_csrf"),weekly_sessions:int=Form(),
         weekly_days:int=Form(),monthly_sessions:int=Form(),monthly_days:int=Form(),
         reason:str=Form(),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin");assert_csrf(request,token)
    vals={"weekly_sessions":weekly_sessions,"weekly_days":weekly_days,
          "monthly_sessions":monthly_sessions,"monthly_days":monthly_days}
    if not (1<=weekly_sessions<=100 and 1<=weekly_days<=7 and
            1<=monthly_sessions<=200 and 1<=monthly_days<=31) or len(reason.strip())<6:
        raise HTTPException(422,"العتبات أو سبب التعديل غير صحيحة")
    previous={x.key:x.value for x in db.scalars(select(Setting)).all()}
    for key,value in vals.items():
        record=db.get(Setting,key)
        if record:record.value=value;record.updated_at=now()
        else:db.add(Setting(key=key,value=value))
    log(db,user,"update_thresholds","settings",0,
        f"before={previous};after={vals};reason={reason[:500]}")
    db.commit();return redirect("/platform/settings")

@app.get("/platform/password",response_class=HTMLResponse)
def own_password_page(request:Request,user=Depends(actor)):
    require_role(user,"coordinator","instructor","educator")
    form_html=form("/platform/password",request.session["csrf"],
       field("current_password","كلمة المرور الحالية","password")+
       field("new_password","كلمة المرور الجديدة (12 حرفًا على الأقل)","password"),"تغيير كلمة المرور")
    return page(request,user,"تغيير كلمة المرور",'<section>'+form_html+'</section>')

@app.post("/platform/password")
def own_password_change(request:Request,token:str=Form(alias="_csrf"),
    current_password:str=Form(),new_password:str=Form(),
    db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"coordinator","instructor","educator");assert_csrf(request,token)
    staff=db.get(Staff,user["id"])
    if not verify_password(current_password,staff.password_hash):
        raise HTTPException(403,"كلمة المرور الحالية غير صحيحة")
    staff.password_hash=hash_password(new_password);staff.session_version+=1
    request.session["session_version"]=staff.session_version
    log(db,user,"password_change","staff",staff.id)
    db.commit();return redirect("/platform")
