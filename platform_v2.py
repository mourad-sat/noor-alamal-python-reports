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
    if not user or not user.enabled:
        raise HTTPException(403, "الحساب معطل أو انتهت صلاحية الجلسة.")
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
        ("تقارير الحصص", "/platform/reports"), ("النقط", "/platform/grades"),
        ("السلوك والانضباط", "/platform/discipline"), ("الإشعارات الورقية", "/platform/notices"),
        ("التنبيهات", "/platform/notifications"),
    ]
    if user["role"] == "admin":
        menu.insert(1, ("الموظفون", "/platform/staff"))
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
        form(f"/platform/years/{y.id}/activate",csrf,"","تفعيل") if not y.archived else "—"] for y in years]
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
    s.password_hash=hash_password(password);log(db,user,"password_reset","staff",s.id);db.commit()
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
