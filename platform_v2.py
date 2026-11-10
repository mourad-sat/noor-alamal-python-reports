"""Arabic operational portal for the two Noor Al Amal centers.

All writes are CSRF protected. Access scope is enforced on the server; hiding a
link in the UI is never considered authorization.
"""
from __future__ import annotations

import base64
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
    Center, Year, Track, Staff, StaffProfile, Assignment, Learner, Enrollment, Slot, Closure,
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
        # A stale or revoked staff cookie must never strand the user on a JSON error.
        # Clear the invalid session so /login can render instead of looping back here.
        request.session.clear()
        raise HTTPException(303, headers={"Location": "/login"})
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

def table(headers, rows, cards=False):
    """Render a table, optionally as labeled cards on small screens."""
    labels = [e(h) for h in headers]
    body = "".join(
        "<tr>" + "".join(
            f'<td data-label="{labels[i] if i < len(labels) else ""}">{cell}</td>'
            for i, cell in enumerate(row)) + "</tr>" for row in rows
    )
    wrapper = "table-scroll table-cards" if cards else "table-scroll"
    return (f'<div class="{wrapper}"><table><thead><tr>' +
            "".join(f"<th>{label}</th>" for label in labels) +
            "</tr></thead><tbody>" +
            (body or f'<tr><td colspan="{len(headers)}">لا توجد بيانات بعد.</td></tr>') +
            "</tbody></table></div>")

def neon_icon(name: str) -> str:
    """Render a fixed local icon, never interpolating untrusted names in markup."""
    allowed = {
        "home", "users", "user-plus", "calendar", "chart", "document",
        "documents", "settings", "bell", "shield", "star", "clock",
        "activity", "folder", "logout", "check", "search", "moon",
    }
    if name not in allowed:
        name = "folder"
    return (
        '<svg class="pv2-icon" aria-hidden="true" focusable="false">'
        f'<use href="/static/neon_icons.svg#{name}"></use></svg>'
    )


def page(request, user, title, body):
    menu = [
        ("الرئيسية", "/platform"), ("السنوات والمسالك", "/platform/years"),
        ("المستفيدون", "/platform/learners"), ("جدولة الحصص", "/platform/schedule"),
        ("تقارير الحصص", "/platform/reports"), ("تقارير أسبوعية وشهرية", "/platform/aggregates"),
        ("إحصائيات الحضور", "/platform/attendance/stats"), ("النقط", "/platform/grades"),
        ("السلوك والانضباط", "/platform/discipline"), ("الإشعارات الورقية", "/platform/notices"),
        ("التنبيهات", "/platform/notifications"),
    ]
    if user["role"] != "admin":
        menu.append(("معلومات بطاقتي", "/platform/profile"))
        menu.append(("تغيير كلمة المرور", "/platform/password"))
    if user["role"] in ("admin","coordinator"):
        menu.append(("استكمال ملفات المستفيدين", "/platform/profile-completeness"))
        menu.append(("مراجعة تصحيحات الحضور", "/platform/attendance/corrections"))
        menu.append(("استيراد Excel", "/platform/import"))
        menu.append(("كشوف النقط PDF", "/platform/exports/grades"))
    if user["role"] == "admin":
        menu.insert(1, ("الموظفون", "/platform/staff"))
        menu.append(("إعدادات التنبيه", "/platform/settings"))
        menu.append(("حماية البيانات والحذف", "/platform/privacy/erase"))
        menu.append(("السجل الإداري", "/platform/audit"))
        menu.append(("التقارير القديمة", "/reports"))
    current_path = request.url.path.rstrip("/") or "/"
    nav_items = []
    nav_icons = {
        "/platform": "home", "/platform/learners": "users",
        "/platform/schedule": "calendar", "/platform/reports": "document",
        "/platform/aggregates": "chart", "/platform/attendance/stats": "activity",
        "/platform/grades": "star", "/platform/staff": "users",
        "/platform/notifications": "bell", "/platform/notices": "documents",
        "/platform/discipline": "shield", "/platform/settings": "settings",
        "/platform/years": "documents", "/platform/profile": "users",
        "/platform/password": "shield", "/platform/audit": "clock",
    }
    for label, url in menu:
        # Keep parent navigation highlighted for detail/edit pages.
        active = current_path == url or (
            url != "/platform" and current_path.startswith(url + "/")
        )
        current = ' aria-current="page"' if active else ""
        nav_items.append(
            f'<a href="{url}"{current}>'
            f'<span class="pv2-nav-icon" aria-hidden="true">{neon_icon(nav_icons.get(url, "folder"))}</span>'
            f'<span>{e(label)}</span></a>'
        )
    nav = "".join(nav_items)
    # Search never leaves the browser; filtering only hides menu links visually.
    nav_search = ('<label class="pv2-nav-search-label" for="pv2-nav-filter">'
                  'البحث في أقسام المنصة</label>'
                  '<input id="pv2-nav-filter" class="pv2-nav-search" type="search" '
                  'placeholder="ابحث عن قسم..." autocomplete="off" '
                  'data-nav-filter="pv2-desktop-links">')
    mobile_search = ('<label class="pv2-nav-search-label" for="pv2-mobile-filter">'
                     'البحث في الأقسام</label>'
                     '<input id="pv2-mobile-filter" class="pv2-nav-search" type="search" '
                     'placeholder="اسم القسم..." autocomplete="off" '
                     'data-nav-filter="pv2-mobile-links">')
    current_section = next((label for label, url in menu if
        current_path == url or (url != "/platform" and current_path.startswith(url + "/"))), title)
    breadcrumbs = (
        '<nav class="pv2-breadcrumbs" aria-label="مسار الصفحة">'
        '<a href="/platform">الرئيسية</a>'
        + (f'<span aria-hidden="true">/</span><span aria-current="page">{e(current_section)}</span>'
           if current_path != "/platform" else "")
        + '</nav>'
    )
    dashboard_top_tools = (
        '<div class="pv2-neon-tools no-print">'
        '<button class="pv2-theme-toggle" type="button" '
        'data-dashboard-theme-toggle aria-label="تبديل مظهر المنصة" '
        'aria-pressed="true" title="تبديل المظهر">☾</button>'
        '<label class="pv2-neon-search-label" for="pv2-dashboard-search">بحث سريع</label>'
        '<input class="pv2-neon-search" id="pv2-dashboard-search" type="search" '
        'placeholder="بحث سريع في الأقسام... (Ctrl+K)" data-nav-filter="pv2-desktop-links">'
        '</div>'
    )
    notice_link = (
        '<a class="pv2-header-notifications" href="/platform/notifications" '
        'aria-label="فتح التنبيهات" title="التنبيهات">'
        + neon_icon("bell") + '</a>'
    )
    username = e(user["name"])
    role_label = e(ROLE_LABELS[user["role"]])
    dashboard_script = '<script src="/static/dashboard_theme.js" defer></script>'
    sidebar_profile = (
        '<div class="pv2-side-profile">'
        '<span class="pv2-side-avatar" aria-hidden="true">●</span>'
        f'<span><strong>{username}</strong><small>{role_label}</small></span>'
        '</div>'
    )
    csrf = post_fields(request.session.get("csrf", ""))
    return HTMLResponse(f'''<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b1426">
<title>{e(title)} | جمعية نور الأمل</title>
<link rel="stylesheet" href="/static/style.css">
<link rel="stylesheet" href="/static/platform_ui.css">
<script src="/static/platform_ui.js" defer></script>
{dashboard_script}
</head><body class="pv2 pv2-neon-shell{" pv2-dashboard" if current_path == "/platform" else ""}">
<a class="pv2-skip" href="#main-content">تجاوز القائمة والوصول إلى المحتوى</a>
<div class="pv2-layout">
<aside class="pv2-sidebar" aria-label="القائمة الرئيسية">
<a class="pv2-brand" href="/platform">
<img src="/static/noor_official_logo.webp" alt="" width="51" height="51">
<span><strong>نور الأمل</strong><small>جمعية نور الأمل</small></span>
</a>
<p class="pv2-nav-title">الأقسام والخدمات</p>
{nav_search}
<nav id="pv2-desktop-links" class="pv2-links" aria-label="التنقل الرئيسي">{nav}</nav>
<p class="pv2-nav-no-results" data-empty-for="pv2-desktop-links" hidden>ما لقيناش قسم مطابق.</p>
{sidebar_profile}
</aside>
<div class="pv2-content-wrap">
<header class="pv2-topbar">
<div class="pv2-topbar-title"><p class="pv2-heading-sub">منصة التسيير الإداري</p><strong>{e(title)}</strong></div>
{dashboard_top_tools}
<div class="pv2-account">
{notice_link}
<span class="pv2-user">{username} · {role_label}</span>
<form class="pv2-logout" method="post" action="/logout">{csrf}<button type="submit">تسجيل الخروج</button></form>
</div>
</header>
<details class="pv2-mobile-menu">
<summary>فتح قائمة أقسام المنصة</summary>
{mobile_search}
<nav id="pv2-mobile-links" class="pv2-links" aria-label="التنقل على الهاتف">{nav}</nav>
<p class="pv2-nav-no-results" data-empty-for="pv2-mobile-links" hidden>ما لقيناش قسم مطابق.</p>
</details>
<main class="pv2-main" id="main-content" tabindex="-1">
{breadcrumbs}
<div class="pv2-page-heading"><h1 class="pv2-page-title">{e(title)}</h1>
<a class="pv2-back-home no-print" href="/platform">لوحة القيادة</a></div>
{body}
</main>
<footer class="pv2-footnote">جمعية نور الأمل · نظام التسيير الإداري</footer>
</div></div></body></html>''')


def by_id(db: Session, model, ids) -> dict:
    """Batch lookup by primary key, safely returning an empty mapping for no IDs."""
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    return {row.id: row for row in db.scalars(
        select(model).where(model.id.in_(wanted))
    ).all()}


def visible_tracks(db: Session, user) -> list:
    """Return only tracks that the actor may access for learner-level pages."""
    rows = db.scalars(select(Track).order_by(
        Track.center_id, Track.year_id, Track.name
    )).all()
    return [
        track for track in rows
        if center_allowed(user, track.center_id) and (
            user["role"] in ("admin", "coordinator")
            or any(assignment.track_id == track.id for assignment in user["assignments"])
        )
    ]


def center_options(db, user):
    rows = db.scalars(select(Center).order_by(Center.id)).all()
    return [(c.id, c.name) for c in rows if center_allowed(user, c.id)]

def tracks_options(db, user):
    rows = [t for t in db.scalars(
        select(Track).order_by(Track.center_id, Track.name)
    ).all() if center_allowed(user, t.center_id)]
    centers = by_id(db, Center, (t.center_id for t in rows))
    years = by_id(db, Year, (t.year_id for t in rows))
    return [(t.id, f"{t.name} · {centers[t.center_id].name} · {years[t.year_id].label}") for t in rows]

def ensure_data(db):
    if not db.scalar(select(func.count()).select_from(Center)):
        for name, area in (("دار الشباب لعيايدة 2", "لعيايدة"), ("مركز حمزة عبد المطلب", "بطانة")):
            db.add(Center(name=name, area=area))
        db.commit()

def dashboard_attendance_snapshot(db: Session, center_ids, today: date | None = None):
    """Real daily rates, based only on recorded attendance in the actor's centers."""
    today = today or date.today()
    dates = [today - timedelta(days=i) for i in range(5, -1, -1)]
    if not center_ids:
        return {"dates": dates, "daily": [None] * len(dates),
                "present": 0, "late": 0, "absent": 0, "sessions": 0,
                "recent": []}
    records = db.execute(
        select(SessionDay.held_on, Attendance.status, func.count(Attendance.id))
        .join(Attendance, Attendance.session_id == SessionDay.id)
        .join(Slot, Slot.id == SessionDay.slot_id)
        .where(Slot.center_id.in_(center_ids),
               SessionDay.held_on >= dates[0], SessionDay.held_on <= today,
               SessionDay.status != "canceled")
        .group_by(SessionDay.held_on, Attendance.status)
    ).all()
    by_day = {day: {"present": 0, "late": 0, "absent": 0} for day in dates}
    for day, status, count in records:
        if day in by_day and status in by_day[day]:
            by_day[day][status] += count
    totals = {status: sum(by_day[day][status] for day in dates)
              for status in ("present", "late", "absent")}
    daily = []
    for day in dates:
        entries = by_day[day]
        recorded = sum(entries.values())
        daily.append(round(100 * (entries["present"] + entries["late"]) / recorded, 1)
                     if recorded else None)
    sessions = db.scalar(
        select(func.count(func.distinct(SessionDay.id)))
        .join(Slot, Slot.id == SessionDay.slot_id)
        .where(Slot.center_id.in_(center_ids),
               SessionDay.held_on >= dates[0], SessionDay.held_on <= today,
               SessionDay.status != "canceled")
    ) or 0
    recent = [
        (day, sum(by_day[day].values()))
        for day in reversed(dates) if sum(by_day[day].values()) > 0
    ][:3]
    return {"dates": dates, "daily": daily, "sessions": sessions,
            "recent": recent, **totals}


def dashboard_neon_charts(snapshot):
    """Accessible inline SVG and ring, strictly from recorded attendance."""
    present, late, absent = (snapshot[k] for k in ("present", "late", "absent"))
    recorded = present + late + absent
    rate = round(100 * (present + late) / recorded, 1) if recorded else None
    present_percent = (present * 100 / recorded) if recorded else 0
    late_percent = (late * 100 / recorded) if recorded else 0
    ring = (
        f'<div class="pv2-neon-ring{" pv2-neon-ring--empty" if not recorded else ""}" role="img" '
        f'aria-label="حاضر: {present}، متأخر: {late}، غائب: {absent}" '
        f'style="--ring-present:{present_percent:.2f}%;'
        f'--ring-late:{present_percent + late_percent:.2f}%">'
        f'<strong>{"—" if rate is None else str(rate) + "%"}</strong>'
        '<small>نسبة الحضور</small></div>'
    )
    legend = "".join(
        f'<span class="pv2-neon-legend-row"><i class="pv2-neon-dot pv2-neon-dot--{kind}"></i>'
        f'{label}<b>{count}</b></span>'
        for kind, label, count in (
            ("present", "حاضر", present),
            ("absent", "غائب", absent),
            ("late", "متأخر", late),
        )
    )
    rate_card = (
        '<section class="pv2-neon-panel pv2-neon-donut">'
        '<h2>حالة الحضور</h2>'
        f'<div class="pv2-neon-donut-body">{ring}'
        f'<div class="pv2-neon-legend">{legend}</div></div>'
        '<p class="pv2-neon-caption">المؤشر مبني على سجلات الأيام الستة الأخيرة فقط.</p>'
        '</section>'
    )
    values = snapshot["daily"]
    available = [(i, value) for i, value in enumerate(values) if value is not None]
    dots = []
    lines = []
    for i, value in available:
        x = 32 + i * 90
        y = 175 - 1.3 * value
        dots.append(f'<circle cx="{x}" cy="{y:.1f}" r="5" />')
    for (start_i, start_value), (end_i, end_value) in zip(available, available[1:]):
        # Only connect adjacent days that both have attendance records.
        if end_i == start_i + 1:
            lines.append(
                f'<line x1="{32 + start_i * 90}" y1="{175 - start_value * 1.3:.1f}" '
                f'x2="{32 + end_i * 90}" y2="{175 - end_value * 1.3:.1f}" />'
            )
    chart = (
        '<svg class="pv2-neon-chart" viewBox="0 0 520 206" role="img" '
        'aria-label="نسبة الحضور اليومية من سجلات الأيام الستة الأخيرة">'
        '<g class="pv2-neon-grid">'
        + "".join(
            f'<line x1="25" y1="{175 - level * 1.3:.1f}" '
            f'x2="500" y2="{175 - level * 1.3:.1f}" />'
            for level in (0, 25, 50, 75, 100)
        )
        + '</g><g class="pv2-neon-line">'
        + "".join(lines) + '</g><g class="pv2-neon-points">'
        + "".join(dots) + '</g></svg>'
        if available else '<div class="pv2-neon-empty">لا توجد سجلات حضور خلال هذه الفترة.</div>'
    )
    day_labels = "".join(
        f'<span title="{day.isoformat()}">{e(day.strftime("%d/%m"))}</span>'
        for day in snapshot["dates"]
    )
    trend_card = (
        '<section class="pv2-neon-panel pv2-neon-trend">'
        '<h2>تطور الحضور خلال 6 أيام</h2>'
        + chart + f'<div class="pv2-neon-days">{day_labels}</div>'
        '<p class="pv2-neon-caption">لا تُوصل الأيام التي لا تتوفر على سجلات حضور.</p>'
        '</section>'
    )
    return trend_card, rate_card, rate


@app.get("/platform", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    """A reference-matched dark dashboard, with truthful role-scoped metrics."""
    ensure_data(db)
    center_ids = [center_id for center_id, _ in center_options(db, user)]
    enrollments = db.scalars(
        select(Enrollment).where(Enrollment.center_id.in_(center_ids))
    ).all()
    pending_count = db.scalar(
        select(func.count()).select_from(Attendance)
        .join(Enrollment, Attendance.enrollment_id == Enrollment.id)
        .where(Enrollment.center_id.in_(center_ids),
               Attendance.status == "absent", Attendance.permit_id.is_(None))
    ) or 0
    notices_count = db.scalar(
        select(func.count()).select_from(Notice)
        .join(Enrollment, Notice.enrollment_id == Enrollment.id)
        .where(Enrollment.center_id.in_(center_ids), Notice.status == "draft")
    ) or 0
    if user["role"] in ("admin", "coordinator"):
        todo_count = db.scalar(
            select(func.count()).select_from(SessionReport)
            .join(SessionDay, SessionReport.session_id == SessionDay.id)
            .join(Slot, SessionDay.slot_id == Slot.id)
            .where(Slot.center_id.in_(center_ids), SessionReport.status == "submitted")
        ) or 0
        todo_text = f"{todo_count} تقرير حصة بانتظار المصادقة"
    else:
        todo_count = db.scalar(
            select(func.count()).select_from(SessionReport).where(
                SessionReport.author_id == user["id"], SessionReport.status == "returned")
        ) or 0
        todo_text = f"{todo_count} تقرير أُعيد إليك للتصحيح"
    centers = db.scalars(
        select(Center).where(Center.id.in_(center_ids)).order_by(Center.id)
    ).all()
    tracks = db.scalars(
        select(Track).where(Track.center_id.in_(center_ids))
    ).all()
    snapshot = dashboard_attendance_snapshot(db, center_ids)
    trend_card, ring_card, attendance_rate = dashboard_neon_charts(snapshot)
    enrollment_totals = {center.id: 0 for center in centers}
    for enrollment in enrollments:
        enrollment_totals[enrollment.center_id] += 1

    def metric(kind, symbol, label, value, hint):
        return (
            f'<div class="pv2-neon-metric pv2-neon-metric--{kind}">'
            f'<span class="pv2-neon-metric-icon" aria-hidden="true">{neon_icon(symbol)}</span>'
            f'<strong>{e(value)}</strong><span>{e(label)}</span>'
            f'<small>{e(hint)}</small></div>'
        )

    week_rate = "—" if attendance_rate is None else f"{attendance_rate}%"
    intro = (
        '<div class="pv2-neon-intro">'
        '<div><h2>👋 مرحبا بك في منصة نور الأمل</h2>'
        '<p>إحصائيات شاملة ومتابعة فعالة للأنشطة.</p></div>'
        '<span class="pv2-neon-period">آخر 6 أيام</span></div>'
    )
    metrics = (
        '<div class="pv2-neon-metrics" aria-label="مؤشرات لوحة القيادة">'
        + metric("learners", "users", "المستفيدون", len(enrollments), "ضمن نطاق صلاحياتك")
        + metric("attendance", "chart", "نسبة الحضور", week_rate, "من سجلات 6 أيام")
        + metric("programs", "documents", "البرامج", len(tracks), "المسالك المتاحة")
        + metric("sessions", "calendar", "الحصص", snapshot["sessions"], "حصص غير ملغاة خلال 6 أيام")
        + '</div>'
    )
    todo = (
        '<a class="pv2-neon-todo" href="/platform/reports">'
        f'<strong>{e(todo_text)}</strong><span>عرض التقارير والمراجعة ←</span></a>'
    ) if todo_count else ""
    actions = [
        ("تسجيل الحضور", "calendar", "/platform/schedule", "green"),
        ("تقرير جديد", "document", "/platform/reports", "blue"),
        ("إضافة مستفيد", "user-plus", "/platform/learners", "purple"),
    ]
    quick = (
        '<section class="pv2-neon-panel pv2-neon-quick"><h2>إجراءات سريعة</h2>'
        '<div class="pv2-neon-actions">'
        + "".join(
            f'<a class="pv2-neon-action pv2-neon-action--{kind}" href="{url}">'
            f'<span aria-hidden="true">{neon_icon(icon)}</span><strong>{e(label)}</strong></a>'
            for label, icon, url, kind in actions
        ) + '</div></section>'
    )
    recent = "".join(
        '<div class="pv2-neon-activity">'
        '<span class="pv2-neon-check" aria-hidden="true">' + neon_icon("check") + '</span>'
        f'<span>سجلات حضور مسجلة في {e(day.isoformat())}</span>'
        f'<small>{count} سجل</small></div>'
        for day, count in snapshot["recent"]
    ) or '<p class="pv2-neon-empty">لا توجد أنشطة حضور مسجلة خلال آخر 6 أيام.</p>'
    activity = (
        '<section class="pv2-neon-panel pv2-neon-activity-panel">'
        '<h2>آخر الأنشطة</h2>' + recent + '</section>'
    )
    center_cards = "".join(
        '<article class="pv2-neon-center">'
        f'<strong>{e(center.name)}</strong><small>{e(center.area)}</small>'
        f'<span>{enrollment_totals[center.id]} مستفيد · '
        f'{sum(track.center_id == center.id for track in tracks)} مسلك</span>'
        '</article>' for center in centers
    )
    centers_section = (
        '<section class="pv2-neon-panel pv2-neon-centers"><h2>المراكز المتاحة</h2>'
        '<div class="pv2-neon-centers-grid">'
        + (center_cards or '<p>لا توجد مراكز متاحة لحسابك.</p>')
        + '</div><p class="pv2-neon-caption">الغياب دون إذن مؤشر متابعة إدارية، وليس قراراً تأديبياً.</p>'
        + f'<p class="pv2-neon-caption">حصص الغياب المعلقة: {pending_count} · '
        f'مسودات الإشعارات: {notices_count}</p></section>'
    )
    content = (
        '<div class="pv2-neon-dashboard">' + intro + todo + metrics +
        '<div class="pv2-neon-charts">' + trend_card + ring_card + '</div>' +
        '<div class="pv2-neon-bottom">' + activity + quick + '</div>' +
        centers_section + '</div>'
    )
    return page(request, user, "لوحة القيادة", content)


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
    track_years = by_id(db, Year, (t.year_id for t in tracks))
    track_centers = by_id(db, Center, (t.center_id for t in tracks))
    trows=[[e(track_years[t.year_id].label),e(track_centers[t.center_id].name),e(t.name)] for t in tracks]
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

# Staff management is reserved for the association's primary administrator.
# Keep staff identity on historical records: deletion is only possible for staff
# with no authored/reviewed documents, grades, incidents, or audit history.
def staff_has_history(db: Session, staff: Staff) -> bool:
    from platform_v2_models import AttendanceCorrection, CenterReport, GradeCorrection
    references = (
        (Assessment, Assessment.staff_id),
        (Slot, Slot.staff_id),
        (SessionReport, SessionReport.author_id),
        (CenterReport, CenterReport.author_id),
        (Incident, Incident.staff_id),
        (Notification, Notification.staff_id),
        (AttendanceCorrection, AttendanceCorrection.requested_by),
        (Audit, Audit.actor),
        (Grade, Grade.approved_by),
        (SessionReport, SessionReport.reviewed_by),
        (CenterReport, CenterReport.reviewed_by),
        (Discipline, Discipline.approved_by),
        (GradeCorrection, GradeCorrection.reviewed_by),
        (AttendanceCorrection, AttendanceCorrection.reviewed_by),
    )
    for model, column in references:
        value = staff.full_name if column.key in ("actor", "approved_by", "reviewed_by") else staff.id
        if db.scalar(select(model.id).where(column == value).limit(1)) is not None:
            return True
    return False


def staff_assignments(db: Session, staff_id: int):
    return db.scalars(select(Assignment).where(Assignment.staff_id == staff_id).order_by(Assignment.id)).all()


def assigned_centers(db: Session, staff_id: int) -> str:
    assignments = staff_assignments(db, staff_id)
    names = list(dict.fromkeys(db.get(Center, a.center_id).name for a in assignments if db.get(Center, a.center_id)))
    return "، ".join(names) if names else "لم يُحدد بعد"


@app.get("/platform/staff", response_class=HTMLResponse)
def staff_page(request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    ensure_data(db)
    csrf = request.session["csrf"]
    staff = db.scalars(select(Staff).order_by(Staff.id)).all()
    add = form("/platform/staff", csrf,
        field("username", "اسم المستخدم") + field("full_name", "الاسم الكامل") +
        choose("role", "الدور", [(k, v) for k, v in ROLE_LABELS.items() if k != "admin"]) +
        field("password", "كلمة مرور أولية (12 حرفًا على الأقل)", "password"), "إنشاء حساب")
    assign = form("/platform/assign", csrf,
        choose("staff_id", "الموظف", [(s.id, s.full_name) for s in staff]) +
        choose("center_id", "المركز", center_options(db, user)) +
        choose("track_id", "المسلك (0 للمنسق)", [(0, "كل المركز / منسق")] + tracks_options(db, user)) +
        choose("subject", "المادة", [("", "التكوين المهني / إدارة المركز")] + [(s, s) for s in SUBJECTS]),
        "إسناد العمل")
    rows = []
    for s in staff:
        actions = (
            f'<div class="form-grid">'
            f'<a class="button" href="/platform/staff/{s.id}/edit">تعديل</a>'
            f'<a class="button" href="/platform/staff/{s.id}/card">طباعة بطاقة</a>'
            f'<a class="button" href="/platform/staff/{s.id}/delete" style="background:#a82d3a">حذف</a>'
            + form(f"/platform/staff/{s.id}/toggle", csrf, "",
                   "تعطيل الحساب" if s.enabled else "تفعيل الحساب")
            + '</div>')
        rows.append([e(s.full_name), e(s.username), e(ROLE_LABELS.get(s.role, s.role)),
                     e(assigned_centers(db, s.id)), e("نشط" if s.enabled else "معطل"), actions])
    intro = (
        '<section><h2>إدارة حسابات الموظفين والمنسقين</h2>'
        '<p class="subtle">يمكنك تعديل المعلومات والمركز، وطباعة بطاقة مهنية، أو حذف الحساب غير المستعمل. '
        'الحساب الذي له سجلات عمل محفوظة يمكن تعطيله فقط لحماية تاريخ الجمعية.</p>'
        '<p><a class="button" href="/platform/staff/cards">طباعة جميع بطاقات الموظفين النشطين</a></p></section>')
    return page(request, user, "حسابات الموظفين", intro +
        f"<section><h2>إنشاء حساب بإذن الإدارة فقط</h2>{add}</section>"
        f"<section><h2>الربط بالمركز والمسلك والمادة</h2>{assign}</section>"
        f"<section>{table(['الاسم', 'المستخدم', 'الدور', 'المركز', 'الحالة', 'الإجراءات'], rows)}</section>")


@app.post("/platform/staff")
def create_staff(request: Request, token: str = Form(alias="_csrf"), username: str = Form(),
                 full_name: str = Form(), role: str = Form(), password: str = Form(),
                 db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    assert_csrf(request, token)
    from app import ADMIN_USERNAME
    username, full_name = username.strip(), full_name.strip()
    if role not in ("coordinator", "instructor", "educator") or not (1 <= len(username) <= 80) or not (1 <= len(full_name) <= 140) or username == ADMIN_USERNAME:
        raise HTTPException(422, "بيانات الحساب غير صحيحة")
    s = Staff(username=username, full_name=full_name, role=role, password_hash=hash_password(password))
    db.add(s)
    try:
        db.flush()
        log(db, user, "create", "staff", s.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "اسم المستخدم موجود")
    return redirect("/platform/staff")


@app.get("/platform/staff/{staff_id:int}/edit", response_class=HTMLResponse)
def edit_staff_page(staff_id: int, request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "الموظف غير موجود")
    assigned = staff_assignments(db, s.id)
    center_id = assigned[0].center_id if s.role == "coordinator" and assigned else 0
    fields = (
        field("username", "اسم المستخدم", value=s.username) +
        field("full_name", "الاسم الكامل", value=s.full_name) +
        choose("role", "الدور", [(k, v) for k, v in ROLE_LABELS.items() if k != "admin"], selected=s.role) +
        choose("center_id", "مركز المنسق", [(0, "بدون مركز")] + center_options(db, user), selected=center_id))
    form_html = form(f"/platform/staff/{s.id}/edit", request.session["csrf"], fields, "حفظ التعديلات")
    note = ('<p class="subtle">يمكن تغيير مركز المنسق هنا. تغيير الدور يحتاج أولاً إلغاء الإسناد القديم، '
            'ولا يسمح به إذا كانت للموظف أعمال موثقة. المكوّن والمنشط تُدار مسالكهما من قسم إسناد العمل.</p>')
    password_form = form(f"/platform/staff/{s.id}/password", request.session["csrf"],
        field("password", "كلمة مرور جديدة (12 حرفًا على الأقل)", "password"), "إعادة تعيين كلمة المرور")
    body = (f'<section><h2>تعديل بيانات: {e(s.full_name)}</h2>{note}{form_html}</section>'
            f'<section><h2>كلمة المرور</h2>{password_form}</section>'
            '<p><a class="button" href="/platform/staff">العودة إلى الموظفين</a></p>')
    return page(request, user, "تعديل الموظف", body)


@app.post("/platform/staff/{staff_id:int}/edit")
def edit_staff(staff_id: int, request: Request, token: str = Form(alias="_csrf"),
               username: str = Form(), full_name: str = Form(), role: str = Form(),
               center_id: int = Form(0), db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    assert_csrf(request, token)
    from app import ADMIN_USERNAME
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "الموظف غير موجود")
    username, full_name = username.strip(), full_name.strip()
    if (role not in ("coordinator", "instructor", "educator") or
        not (1 <= len(username) <= 80) or not (1 <= len(full_name) <= 140) or
        username == ADMIN_USERNAME or center_id < 0):
        raise HTTPException(422, "بيانات الموظف غير صحيحة")
    assigned = staff_assignments(db, s.id)
    if role != s.role and (assigned or staff_has_history(db, s)):
        raise HTTPException(409, "لا يمكن تغيير الدور مع وجود إسناد أو سجل عمل؛ ألغ الإسناد أولاً إن لم تكن هناك أعمال موثقة")
    if role == "coordinator":
        if center_id and not db.get(Center, center_id):
            raise HTTPException(422, "المركز غير موجود")
        if any(a.track_id is not None or a.subject for a in assigned):
            raise HTTPException(409, "يجب إزالة إسناد المسالك قبل التحويل لمنسق")
        other = db.scalar(select(Assignment).join(Staff, Assignment.staff_id == Staff.id).where(
            Assignment.center_id == center_id, Staff.role == "coordinator", Staff.id != s.id).limit(1)) if center_id else None
        if other:
            raise HTTPException(409, "هذا المركز له منسق آخر بالفعل")
        old_center_id = assigned[0].center_id if assigned else 0
        if center_id != old_center_id:
            if staff_has_history(db, s):
                raise HTTPException(409, "لهذا المنسق أعمال موثقة؛ احتفظ بحسابه التاريخي وأنشئ حسابًا جديدًا للمركز الآخر")
            for a in assigned:
                db.delete(a)
            db.flush()
            if center_id:
                db.add(Assignment(staff_id=s.id, center_id=center_id, track_id=None, subject=""))
    elif role != s.role and assigned:
        raise HTTPException(409, "يجب إزالة الإسناد القديم قبل تغيير الدور")
    sensitive_changed = s.username != username or s.role != role or (
        role == "coordinator" and (assigned[0].center_id if assigned else 0) != center_id)
    s.username = username
    s.full_name = full_name
    s.role = role
    if sensitive_changed:
        s.session_version += 1
    log(db, user, "edit", "staff", s.id, "تحديث بيانات الموظف أو مركز إسناده")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "اسم المستخدم موجود أو هناك تعارض في الإسناد")
    return redirect("/platform/staff")


@app.get("/platform/staff/{staff_id:int}/delete", response_class=HTMLResponse)
def staff_delete_page(staff_id: int, request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "الموظف غير موجود")
    history = staff_has_history(db, s)
    if history:
        body = (f'<section><h2>لا يمكن حذف {e(s.full_name)}</h2>'
                '<p>للموظف أو المنسق بيانات عمل مؤرشفة. حفاظًا على التقارير والنقط والمصادقات، '
                'استعمل زر «تعطيل الحساب» من صفحة الموظفين بدلاً من الحذف.</p></section>')
    else:
        fields = (field("confirm_username", f"للتأكيد، اكتب اسم المستخدم: {s.username}") +
                  field("reason", "سبب الحذف (إلزامي)"))
        body = (f'<section><h2 class="danger">حذف حساب: {e(s.full_name)}</h2>'
                '<p>سيُحذف هذا الحساب نهائيًا مع إسناداته فقط، ولن يُحذف أي تقرير أو ملف مستفيد.</p>' +
                form(f"/platform/staff/{s.id}/delete", request.session["csrf"], fields, "تأكيد حذف الحساب") +
                '</section>')
    return page(request, user, "حذف حساب موظف",
                body + '<p><a class="button" href="/platform/staff">إلغاء والعودة إلى الموظفين</a></p>')


@app.post("/platform/staff/{staff_id:int}/delete")
def delete_staff(staff_id: int, request: Request, token: str = Form(alias="_csrf"),
                 confirm_username: str = Form(), reason: str = Form(),
                 db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    assert_csrf(request, token)
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "الموظف غير موجود")
    if confirm_username.strip() != s.username or not reason.strip() or len(reason.strip()) > 500:
        raise HTTPException(422, "اسم المستخدم غير مطابق أو سبب الحذف غير صالح")
    if staff_has_history(db, s):
        raise HTTPException(409, "لا يمكن الحذف بسبب وجود سجلات عمل؛ يمكنك تعطيل الحساب")
    for a in staff_assignments(db, s.id):
        db.delete(a)
    profile = db.get(StaffProfile, s.id)
    if profile:
        db.delete(profile)
    db.flush()
    db.delete(s)
    log(db, user, "delete", "staff", staff_id, reason.strip())
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "لا يمكن الحذف بسبب وجود بيانات مرتبطة بهذا الحساب")
    return redirect("/platform/staff")


def staff_cards_html(staff: list[Staff], db: Session, back_to: str = "/platform/staff") -> HTMLResponse:
    cards = []
    for s in staff:
        if not s.enabled:
            continue
        profile = db.get(StaffProfile, s.id)
        centers = e(assigned_centers(db, s.id))
        specialty = e(profile.specialty) if profile and profile.specialty else ""
        work_phone = e(profile.work_phone) if profile and profile.work_phone else ""
        work_email = e(profile.work_email) if profile and profile.work_email else ""
        portrait = (
            f'<img class="portrait" alt="صورة الموظف" src="data:image/jpeg;base64,{base64.b64encode(profile.photo_data).decode("ascii")}">'
            if profile and profile.photo_data and profile.photo_mime == "image/jpeg"
            else '<div class="portrait-placeholder" aria-hidden="true">الصورة<br>اختيارية</div>')
        extra = (
            (f'<p>التخصص: {specialty}</p>' if specialty else '') +
            (f'<p>الهاتف المهني: <bdi>{work_phone}</bdi></p>' if work_phone else '') +
            (f'<p>البريد المهني: <bdi>{work_email}</bdi></p>' if work_email else '')
        )
        cards.append(
            '<article class="employee-card">'
            '<header><img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل">'
            '<div><strong>جمعية نور الأمل</strong><small>برنامج الفرصة الثانية – الجيل الجديد</small></div></header>'
            '<div class="employee-content"><span class="tag">بطاقة مهنية داخلية</span>'
            f'<div class="employee-details">{portrait}<div class="employee-information">'
            f'<h2>{e(s.full_name)}</h2>'
            f'<p>{e(ROLE_LABELS.get(s.role, s.role))}</p>'
            f'<p>المركز: {centers}</p>{extra}'
            '</div></div></div>'
            f'<footer><span>رقم الموظف: {s.id:05d}</span>'
            f'<span>تاريخ الإصدار: {date.today().strftime("%Y/%m/%d")}</span></footer>'
            '</article>')
    cards_html = "".join(cards) if cards else '<p>لا توجد حسابات نشطة لطباعة بطاقاتها.</p>'
    if back_to not in ("/platform/staff", "/platform/profile"):
        back_to = "/platform/staff"
    return HTMLResponse("""<!doctype html><html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>بطاقات الموظفين | جمعية نور الأمل</title>
<style>
*{box-sizing:border-box}
body{font-family:Tahoma,Arial,sans-serif;color:#16324a;background:#eef4f7;margin:0;padding:22px}
.toolbar{display:flex;align-items:center;gap:12px;margin-bottom:22px}
button,a.button{font:inherit;border:0;border-radius:8px;padding:10px 16px;background:#145485;color:white;cursor:pointer;text-decoration:none}
.cards{display:flex;gap:10mm;flex-wrap:wrap}
.employee-card{width:85.6mm;height:54mm;flex:0 0 85.6mm;background:white;border:1px solid #acc6d3;
border-radius:4mm;overflow:hidden;display:flex;flex-direction:column;break-inside:avoid;page-break-inside:avoid}
.employee-card header{display:flex;align-items:center;gap:3mm;background:#125083;color:white;padding:3mm 4mm;height:15mm;flex:0 0 15mm}
.employee-card header img{width:13mm;height:12mm;object-fit:contain;background:white;border-radius:2mm}
.employee-card header strong{display:block;font-size:12px}
.employee-card header small{display:block;font-size:8px}
.employee-content{padding:1.5mm 3mm;flex:1;overflow:hidden}
.employee-details{display:flex;gap:2mm;margin-top:.7mm;align-items:flex-start;min-width:0}
.portrait,.portrait-placeholder{width:18mm;height:25mm;flex:0 0 18mm;border-radius:1.5mm;border:1px solid #c3d3dd;object-fit:cover}
.portrait-placeholder{display:flex;align-items:center;justify-content:center;background:#f1f5f8;color:#82939f;font-size:8px;text-align:center}
.employee-information{min-width:0;flex:1;overflow:hidden}
.employee-information h2{font-size:12px;margin:0 0 .4mm;line-height:1.2}
.employee-information p{margin:0;font-size:8px;line-height:1.3;overflow-wrap:anywhere}
.tag{font-size:8px;color:#11764c;font-weight:bold}
.employee-card footer{background:#e6f5ed;border-top:1px solid #b2d8bf;display:flex;justify-content:space-between;
padding:1.6mm 3mm;font-size:8px;flex:0 0 auto}
@media print{
@page{size:A4 portrait;margin:12mm}
body{margin:0;padding:0;background:white}
.toolbar{display:none}
.cards{gap:6mm}
.employee-card{border-color:#777;print-color-adjust:exact;-webkit-print-color-adjust:exact}
}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">طباعة البطاقات أو حفظ PDF</button>
<a class="button" href=""" + f'"{back_to}">العودة</a>' + """
<small>تظهر المعلومات المهنية التي أدخلها صاحب البطاقة، ولا تظهر كلمات المرور.</small></div>
<div class="cards">""" + cards_html + "</div></body></html>")


@app.get("/platform/staff/cards", response_class=HTMLResponse)
def all_staff_cards(db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    staff = db.scalars(select(Staff).where(Staff.enabled.is_(True)).order_by(Staff.id)).all()
    return staff_cards_html(staff, db)


@app.get("/platform/staff/{staff_id:int}/card", response_class=HTMLResponse)
def one_staff_card(staff_id: int, db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    staff = db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(404, "الموظف غير موجود")
    if not staff.enabled:
        raise HTTPException(409, "لا يمكن إصدار بطاقة لحساب معطل")
    return staff_cards_html([staff], db)


@app.post("/platform/staff/{staff_id}/toggle")
def toggle_staff(staff_id: int, request: Request, token: str = Form(alias="_csrf"),
                 db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    assert_csrf(request, token)
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404)
    s.enabled = not s.enabled
    s.session_version += 1
    log(db, user, "staff_toggle", "staff", s.id, str(s.enabled))
    db.commit()
    return redirect("/platform/staff")


@app.post("/platform/staff/{staff_id}/password")
def change_staff_password(staff_id: int, request: Request, token: str = Form(alias="_csrf"),
                          password: str = Form(), db: Session = Depends(get_db), user=Depends(actor)):
    require_role(user, "admin")
    assert_csrf(request, token)
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404)
    s.password_hash = hash_password(password)
    s.session_version += 1
    log(db, user, "password_reset", "staff", s.id)
    db.commit()
    return redirect("/platform/staff/{}/edit".format(s.id))


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


@app.get("/platform/learners", response_class=HTMLResponse)
def learners_page(request: Request, track_id: int | None = None,
                  db: Session = Depends(get_db), user=Depends(actor)):
    """Choose a center and track first; never show an unfiltered learner roster."""
    ensure_data(db)
    allowed_tracks = visible_tracks(db, user)
    if track_id is None:
        sections = [
            '<section class="pv2-roster-intro"><h2>اختيار المركز والمسلك</h2>'
            '<p class="subtle">لن تظهر أسماء المستفيدين حتى تدخل إلى المسلك الذي تريده. '
            'اختر المركز ثم اضغط على اسم المسلك لعرض لائحته فقط.</p></section>'
        ]
        track_years = by_id(db, Year, (t.year_id for t in allowed_tracks))
        for center_id, center_name in center_options(db, user):
            center_tracks = [track for track in allowed_tracks if track.center_id == center_id]
            buttons = "".join(
                '<a class="pv2-track-link" '
                f'href="/platform/learners?track_id={track.id}">'
                f'{e(track.name)} <small>({e(track_years[track.year_id].label)})</small></a>'
                for track in center_tracks
            )
            sections.append(
                f'<section class="pv2-track-section"><h2>{e(center_name)}</h2><div class="pv2-track-grid">'
                + (buttons or '<p class="subtle">لم تُضف مسالك متاحة لهذا المركز بعد.</p>')
                + '</div></section>'
            )
        return page(request, user, "المستفيدون حسب المركز والمسلك", "".join(sections))

    selected = next((track for track in allowed_tracks if track.id == track_id), None)
    if selected is None:
        # Never leak learner counts, names or metadata for other centers/tracks.
        raise HTTPException(403, "هذا المسلك غير متاح لحسابك.")
    center = db.get(Center, selected.center_id)
    year = db.get(Year, selected.year_id)
    enrolls = db.scalars(
        select(Enrollment).where(Enrollment.track_id == selected.id)
        .order_by(Enrollment.id.desc()).limit(800)
    ).all()
    rows = []
    enrollment_ids = [en.id for en in enrolls]
    learners_by_id = by_id(db, Learner, (en.learner_id for en in enrolls))
    pending_by_enrollment = dict(db.execute(
        select(Attendance.enrollment_id, func.count()).where(
            Attendance.enrollment_id.in_(enrollment_ids),
            Attendance.status == "absent", Attendance.permit_id.is_(None)
        ).group_by(Attendance.enrollment_id)
    ).all()) if enrollment_ids else {}
    excluded_ids = disciplined_enrollment_ids(db, enrollment_ids)
    for en in enrolls:
        learner = learners_by_id[en.learner_id]
        pending = pending_by_enrollment.get(en.id, 0)
        disciplinary = en.id in excluded_ids
        status = ('<span class="danger">غياب معلق</span>' if pending
                  else '<span class="good">لا غياب معلق</span>')
        if disciplinary:
            status += ' · <span class="danger">إقصاء تأديبي</span>'
        details = f'<a href="/platform/learners/{en.id}">فتح الملف</a>'
        if user["role"] in ("admin", "coordinator"):
            details += f' · <a href="/platform/learners/{en.id}/card">طباعة البطاقة</a>'
        rows.append([e(learner.full_name), e(learner.source_code) or "—", status, details])
    title = f'{center.name} · {selected.name} · {year.label}'
    body = (f'<section class="pv2-roster-intro"><h2>{e(title)}</h2>'
            f'<p class="subtle">عدد المستفيدين في هذا المسلك: {len(rows)}'
            ' · اللائحة مقتصرة على هذا المسلك فقط.</p>'
            '<a class="button" href="/platform/learners">العودة إلى اختيار المسالك</a>'
            '</section>')
    body += ('<section class="pv2-roster-list"><h2>لائحة المستفيدين</h2>'
             + report_filter_box("learners-roster-table", "بحث بالاسم أو الرمز")
             + '<div id="learners-roster-table">'
             + table(["الاسم الكامل", "الرمز الأصلي", "الحالة", "الإجراءات"], rows, cards=True)
             + '</div></section>')
    if user["role"] in ("admin", "coordinator") and not year.archived:
        csrf = request.session["csrf"]
        registration_fields = (
            field("full_name", "اسم المستفيد") +
            choose("gender", "الجنس", [("", "غير محدد"), ("ذكر", "ذكر"), ("أنثى", "أنثى")]) +
            field("born_on", "تاريخ الازدياد", "date", False) +
            field("birthplace", "مكان الازدياد", required=False) +
            field("phone", "هاتف المستفيد", required=False) +
            field("address", "العنوان", required=False) +
            field("guardian_name", "اسم ولي الأمر", required=False) +
            field("guardian_relationship", "صلة القرابة", required=False) +
            field("guardian_phone", "هاتف ولي الأمر", required=False) +
            f'<input type="hidden" name="year_id" value="{year.id}">' +
            f'<input type="hidden" name="track_id" value="{selected.id}">'
        )
        body += ('<section class="pv2-roster-register"><details><summary>تسجيل مستفيد جديد في هذا المسلك</summary>'
                 '<p class="subtle">يمكن إدخال المعلومات المتوفرة الآن، واستكمال الحقول الأخرى لاحقاً.</p>'
                 + form("/platform/learners", csrf, registration_fields, "تسجيل المستفيد")
                 + '</details></section>')
    return page(request, user, "لائحة مستفيدي المسلك", body)


def disciplinary_status(db:Session,en:Enrollment,at:date|None=None):
    at=at or date.today()
    records=db.scalars(select(Discipline).where(Discipline.enrollment_id==en.id,
                                               Discipline.status.in_(("effective","early_pending")))).all()
    for d in records:
        if not d.return_authorized and (d.excluded_from is None or d.excluded_from<=at):
            # At the end date, a separate reasoned return authorization remains mandatory.
            return True
    return False

def disciplined_enrollment_ids(db: Session, enrollment_ids, at: date | None = None) -> set:
    """Batch the existing disciplinary status rule without changing its meaning."""
    ids = list(enrollment_ids)
    if not ids:
        return set()
    at = at or date.today()
    records = db.scalars(select(Discipline).where(
        Discipline.enrollment_id.in_(ids),
        Discipline.status.in_(("effective", "early_pending"))
    )).all()
    return {record.enrollment_id for record in records
            if not record.return_authorized and
            (record.excluded_from is None or record.excluded_from <= at)}


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
    # Initial registration accepts partial information; complete missing contacts later.
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
        from platform_v2_models import LearnerPhoto
        has_photo = db.get(LearnerPhoto, l.id) is not None
        preview_img = (
            f'<img src="/platform/learners/{en.id}/photo/file" '
            'alt="صورة المستفيد" style="width:120px;height:150px;object-fit:cover;border-radius:9px">'
            if has_photo else '<span class="subtle">لم تُرفع صورة بعد.</span>'
        )
        body += (
            '<section><h2>الصورة والبطاقات</h2><div class="form-grid">'
            f'{preview_img}'
            '<div>'
            f'<p><a class="button" href="/platform/learners/{en.id}/edit">تعديل واستكمال البيانات</a> '
            f'<a class="button" href="/platform/learners/{en.id}/card">طباعة بطاقة A4 المفصلة</a> '
            f'<a class="button" href="/platform/learners/{en.id}/card/mini">طباعة البطاقة الصغيرة</a></p>'
            '<p class="subtle">الصورة تظهر أعلى يمين البطاقتين، ولا يستطيع رفعها إلا المسؤول المخوّل للمركز.</p>'
            f'<form method="post" action="/platform/learners/{en.id}/photo" '
            'enctype="multipart/form-data" class="form-grid">'
            f'{post_fields(csrf)}'
            '<label>رفع صورة المستفيد (JPG أو PNG أو WEBP، حتى 2 ميغابايت)'
            '<input type="file" name="photo" accept="image/jpeg,image/png,image/webp" required></label>'
            '<button type="submit">حفظ الصورة</button></form>'
        )
        if has_photo:
            body += (
                f'<form method="post" action="/platform/learners/{en.id}/photo/delete" class="form-grid">'
                f'{post_fields(csrf)}'
                '<label>تأكيد إزالة الصورة'
                '<select name="confirmation"><option value="">اختر التأكيد</option>'
                '<option value="حذف الصورة">حذف الصورة</option></select></label>'
                '<button type="submit">إزالة الصورة المحفوظة</button></form>'
            )
        body += '</div></div></section>'
        if l.born_on and (date.today()-l.born_on).days < 18*365 and not l.guardian_phone:
            body += '<section><p class="danger">هاتف ولي الأمر لم يُسجل بعد؛ يرجى استكماله لاحقًا من زر التعديل.</p></section>'
    if user["role"] in ("admin","coordinator"):
        body+=f'<section><h2>المعلومات العائلية (مقيدة)</h2><p>الرمز الأصلي: {e(l.source_code) if l.source_code else "غير متوفر"} · ولي الأمر: {e(l.guardian_name)} · هاتف: {e(l.guardian_phone)} · الصفة: {e(l.guardian_relationship)}</p></section>'
    arows=[[e(s.held_on),e(a.status),e("مغطى بإذن" if a.permit_id else "معلّق" if a.status=="absent" else "—")] for a,s in atts]
    body+=f'<section><h2>سجل الحضور والغياب</h2>{table(["التاريخ","الحالة","إذن الدخول"],arows)}</section>'
    if user["role"]=="coordinator" and pending:
        body+='<section><h2>إذن دخول معلل يغطي الغيابات المعلقة</h2>'+form(f"/platform/learners/{en.id}/permit",csrf,
          area("reason","التبرير الإلزامي"),"إصدار إذن الدخول")+'</section>'
    results=transcript(db,en)
    items=[[e(k),e(v) if v is not None else "غير مكتمل"] for k,v in results.items()]
    body+='<section><h2>المعدلات المعتمدة (من 20)</h2>'+table(["المادة / المعدل","النقطة"],items)+'</section>'
    body += f'<p><a class="button" href="/platform/learners?track_id={en.track_id}">العودة إلى لائحة المسلك</a></p>'
    return page(request,user,"ملف المستفيد",body)


@app.get("/platform/learners/{enrollment_id:int}/card", response_class=HTMLResponse)
def learner_print_card(enrollment_id: int, db: Session = Depends(get_db),
                       user=Depends(actor)):
    """Full learner information card, restricted to administrators/coordinators."""
    require_role(user, "admin", "coordinator")
    enrollment = learner_scope(db, user, enrollment_id)
    learner = db.get(Learner, enrollment.learner_id)
    from platform_v2_learner_cards import render_card
    return render_card(db, enrollment, learner, kind="a4")


@app.get("/platform/learners/{enrollment_id:int}/edit", response_class=HTMLResponse)
def learner_edit_page(enrollment_id:int, request:Request,
                      db:Session=Depends(get_db), user=Depends(actor)):
    require_role(user, "admin", "coordinator")
    en = learner_scope(db,user,enrollment_id)
    l = db.get(Learner,en.learner_id)
    fields = (
        field("full_name","الاسم الكامل",value=l.full_name) +
        field("source_code","الرمز الأصلي (إن وجد)",required=False,value=l.source_code) +
        choose("gender","الجنس",[("","غير محدد"),("ذكر","ذكر"),("أنثى","أنثى")],selected=l.gender) +
        field("born_on","تاريخ الازدياد","date",False,l.born_on.isoformat() if l.born_on else "") +
        field("birthplace","مكان الازدياد",required=False,value=l.birthplace) +
        field("phone","هاتف المستفيد",required=False,value=l.phone) +
        field("address","العنوان",required=False,value=l.address) +
        field("guardian_name","اسم ولي الأمر",required=False,value=l.guardian_name) +
        field("guardian_relationship","صلة القرابة",required=False,value=l.guardian_relationship) +
        field("guardian_phone","هاتف ولي الأمر",required=False,value=l.guardian_phone)
    )
    content = (f'<section><h2>استكمال ملف: {e(l.full_name)}</h2>'
               '<p class="subtle">يمكن استكمال البيانات الناقصة لاحقًا. لا تغيّر هذه الصفحة السنة أو المركز أو المسلك.</p>' +
               form(f"/platform/learners/{en.id}/edit",request.session["csrf"],fields,"حفظ البيانات") +
               '</section>' + f'<p><a class="button" href="/platform/learners/{en.id}">العودة إلى ملف المستفيد</a></p>')
    return page(request,user,"تعديل ملف المستفيد",content)


@app.post("/platform/learners/{enrollment_id:int}/edit")
def learner_edit(enrollment_id:int,request:Request,token:str=Form(alias="_csrf"),
                 full_name:str=Form(),source_code:str=Form(""),gender:str=Form(""),born_on:date|None=Form(None),
                 birthplace:str=Form(""),phone:str=Form(""),address:str=Form(""),
                 guardian_name:str=Form(""),guardian_relationship:str=Form(""),
                 guardian_phone:str=Form(""),db:Session=Depends(get_db),user=Depends(actor)):
    require_role(user,"admin","coordinator")
    assert_csrf(request,token)
    en=learner_scope(db,user,enrollment_id)
    l=db.get(Learner,en.learner_id)
    full_name=full_name.strip()
    source_code=source_code.strip()
    if (len(full_name)<3 or len(full_name)>180 or full_name[:1] in ("=","+","-","@")
        or len(source_code)>60 or source_code[:1] in ("=","+","-","@")
        or gender not in ("","ذكر","أنثى","M","F","m","f")
        or (born_on and born_on>date.today()) or len(birthplace.strip())>120
        or len(phone.strip())>45 or len(address.strip())>240
        or len(guardian_name.strip())>180 or len(guardian_relationship.strip())>70
        or len(guardian_phone.strip())>45):
        raise HTTPException(422,"البيانات غير صالحة؛ راجع الاسم وتاريخ الميلاد والخانات.")
    existing=db.scalar(select(Learner.id).where(Learner.id!=l.id,
       Learner.full_name==full_name,Learner.born_on==born_on).limit(1))
    if existing is not None:raise HTTPException(409,"يوجد ملف آخر بنفس الاسم وتاريخ الازدياد")
    if source_code and db.scalar(select(Learner.id).where(Learner.id!=l.id,Learner.source_code==source_code).limit(1)) is not None:
        raise HTTPException(409,"الرمز الأصلي مستعمل في ملف مستفيد آخر")
    l.full_name=full_name
    l.source_code=source_code
    l.gender=gender
    l.born_on=born_on
    l.birthplace=birthplace.strip()
    l.phone=phone.strip()
    l.address=address.strip()
    l.guardian_name=guardian_name.strip()
    l.guardian_relationship=guardian_relationship.strip()
    l.guardian_phone=guardian_phone.strip()
    log(db,user,"update","learner",l.id,"استكمال معلومات الملف")
    db.commit()
    return redirect(f"/platform/learners/{en.id}")


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
    query=select(Slot).where(Slot.year_id==slot.year_id,Slot.weekday==slot.weekday,
                            Slot.canceled.is_(False),
                            or_(Slot.staff_id==slot.staff_id,Slot.track_id==slot.track_id))
    for other in db.scalars(query).all():
        if other.id==slot.id or not overlaps(other,slot):
            continue
        # An exceptional make-up may replace a recurring occurrence only after
        # that exact date has been cancelled and audited by the coordinator.
        if slot.specific_date and not other.specific_date:
            canceled=db.scalar(select(SessionDay).where(
                SessionDay.slot_id==other.id,
                SessionDay.held_on==slot.specific_date,
                SessionDay.status=="canceled"))
            if canceled:
                continue
        return other
    return None

@app.get("/platform/schedule",response_class=HTMLResponse)
def schedule_page(request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    csrf=request.session["csrf"];ensure_data(db)
    slots=[s for s in db.scalars(select(Slot).order_by(Slot.weekday,Slot.starts_at)).all()
           if center_allowed(user,s.center_id) and (user["role"] in ("admin","coordinator") or s.staff_id==user["id"])]
    rows=[]
    slot_centers=by_id(db,Center,(item.center_id for item in slots))
    slot_tracks=by_id(db,Track,(item.track_id for item in slots))
    slot_staff=by_id(db,Staff,(item.staff_id for item in slots))
    for s in slots:
        c=slot_centers[s.center_id];t=slot_tracks[s.track_id];staff=slot_staff[s.staff_id]
        action=form("/platform/sessions",csrf,field("slot_id","رقم الحصة",value=s.id)+field("held_on","التاريخ","date"),"فتح الحصة") if not s.canceled else "ملغاة"
        if user["role"]=="coordinator" and not s.canceled:
            action+=form(f"/platform/schedule/{s.id}/cancel-day",csrf,
                field("held_on","تاريخ الحصة الملغاة","date")+
                area("reason","سبب الإلغاء / التأجيل المعلل"),
                "إلغاء حصة واحدة دون احتساب الغياب")
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
    if other:
        conflict = "المؤطر" if other.staff_id==slot.staff_id else "المسلك"
        raise HTTPException(409,f"تعارض في التوقيت: {conflict} مرتبط بحصة رقم {other.id} في الوقت نفسه.")
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
    if item and item.status=="canceled":
        raise HTTPException(409,"الحصة ملغاة لهذا التاريخ ولا يُحتسب الغياب")
    if not item:
        if user["id"]!=slot.staff_id and user["role"] not in ("admin","coordinator"):
            raise HTTPException(403)
        item=SessionDay(slot_id=slot.id,held_on=held_on)
        db.add(item);db.flush();log(db,user,"open_session","session",item.id);db.commit()
    return redirect(f"/platform/sessions/{item.id}")

def session_report_editor(session_id: int, csrf: str, report, default_subject: str) -> str:
    """Accessible, prefilled editor; saving and submitting remain separate POST actions."""
    def report_textarea(name: str, label: str, hint: str, rows: int = 4) -> str:
        value = e(getattr(report, name, "") if report else "")
        return (
            f'<label class="pv2-report-field" for="session-report-{name}">'
            f'<span>{e(label)}</span><small>{e(hint)}</small>'
            f'<textarea id="session-report-{name}" name="{name}" rows="{rows}" '
            f'maxlength="10000">{value}</textarea></label>'
        )
    subject = e(report.subject if report else default_subject)
    is_returned = bool(report and report.status == "returned")
    notice = (
        '<div class="pv2-session-report-return" role="status">'
        '<strong>أعيد التقرير للتصحيح</strong>'
        f'<p>{e(report.review_comment) if report.review_comment else "راجع محتوى التقرير ثم احفظه قبل إرساله مجدداً."}</p>'
        '</div>'
    ) if is_returned else ""
    guide = (
        '<ol class="pv2-report-steps">'
        '<li><strong>تعمير التقرير</strong><span>وثّق الأهداف والأنشطة والنتائج.</span></li>'
        '<li><strong>حفظ المسودة</strong><span>يمكنك الرجوع إليها وتعديلها لاحقاً.</span></li>'
        '<li><strong>إرسال للمراجعة</strong><span>بعد الحفظ، أرسل التقرير إلى المنسق.</span></li>'
        '</ol>'
    )
    fields = (
        '<div class="pv2-report-editor-grid">'
        '<label class="pv2-report-field pv2-report-field--wide" for="session-report-subject">'
        '<span>عنوان التقرير <em>إلزامي</em></span>'
        '<small>عنوان واضح يختصر موضوع الحصة.</small>'
        f'<input id="session-report-subject" name="subject" type="text" '
        f'value="{subject}" maxlength="200" required></label>'
        + report_textarea("objectives", "الأهداف", "ما الذي أردتم تحقيقه خلال الحصة؟")
        + report_textarea("activities", "الأنشطة المنجزة", "ما الأنشطة والخطوات التي أُنجزت فعلياً؟", 5)
        + report_textarea("results", "النتائج والمهارات المكتسبة", "ما الذي تعلمه أو أنجزه المستفيدون؟", 5)
        + report_textarea("challenges", "الصعوبات", "اذكر الصعوبات إن وُجدت، أو اترك الحقل فارغاً.")
        + report_textarea("recommendations", "التوصيات", "ما التحسينات أو الإجراءات المقترحة للحصة المقبلة؟")
        + '</div>'
    )
    save = (
        '<form class="pv2-session-report-form" method="post" '
        f'action="/platform/sessions/{session_id}/report">'
        + post_fields(csrf) + fields
        + '<div class="pv2-report-save-actions"><button type="submit">حفظ مسودة التقرير</button>'
        '<span>الحفظ لا يرسل التقرير للمصادقة.</span></div></form>'
    )
    submit = (
        '<div class="pv2-report-submit">'
        '<div><strong>الخطوة التالية: المراجعة</strong>'
        '<p>بعد التأكد من حفظ التغييرات، يمكنك إرسال التقرير إلى المنسق للمصادقة.</p></div>'
        + form(f"/platform/reports/{report.id}/submit", csrf, "", "إرسال للمراجعة")
        + '</div>'
    ) if report else (
        '<p class="pv2-report-hint">احفظ المسودة أولاً حتى يظهر زر إرسال التقرير للمراجعة.</p>'
    )
    return (
        '<section class="pv2-session-report-editor" aria-labelledby="session-report-title">'
        '<div class="pv2-report-editor-head"><div>'
        '<p class="pv2-report-eyebrow">تقرير الحصة</p>'
        '<h2 id="session-report-title">توثيق الحصة ومتابعة المصادقة</h2>'
        '<p>اكتب المعلومات تدريجياً، واحفظ المسودة قبل إرسالها للمراجعة.</p>'
        '</div><span class="pv2-report-editor-state">'
        + ("أعيد للتصحيح" if is_returned else "مسودة قابلة للتعديل")
        + '</span></div>' + notice + guide + save + submit + '</section>'
    )


def attendance_choice_form(session_id: int, enrollment_id: int, csrf: str,
                           current_status: str | None, pending_absence: bool) -> str:
    """Explicit one-person attendance action; keeps the original secured POST route."""
    choices = (("present", "حاضر"), ("absent", "غائب"), ("late", "متأخر"))
    radios = []
    for value, label in choices:
        checked = ' checked' if current_status == value else ""
        # A prior unresolved absence blocks another present/late mark server-side.
        disabled = ' disabled' if pending_absence and value != "absent" else ""
        radios.append(
            '<label class="pv2-attendance-option">'
            f'<input type="radio" name="status" value="{value}" required{checked}{disabled}>'
            f'<span>{label}</span></label>'
        )
    hint = (
        '<p class="pv2-attendance-hint danger">يوجد غياب معلّق؛ '
        'يلزم إذن المنسق قبل تسجيل الحضور أو التأخر.</p>'
    ) if pending_absence else ""
    return (
        f'<form class="pv2-attendance-form" method="post" '
        f'action="/platform/sessions/{session_id}/attendance">'
        + post_fields(csrf)
        + f'<input type="hidden" name="enrollment_id" value="{enrollment_id}">'
        + '<fieldset class="pv2-attendance-options"><legend>تحديد الحضور</legend>'
        + "".join(radios) + '</fieldset>' + hint
        + '<button type="submit">حفظ الحالة</button></form>'
    )


def attendance_summary(enrollments, marks: dict) -> str:
    """Show only this session's enrollment counts; do not imply absent for unmarked."""
    counts = {"present": 0, "absent": 0, "late": 0, "unmarked": 0}
    for enrollment in enrollments:
        mark = marks.get(enrollment.id)
        status = mark.status if mark else ""
        if status in ("present", "absent", "late"):
            counts[status] += 1
        else:
            counts["unmarked"] += 1
    labels = (
        ("present", "حاضر"), ("absent", "غائب"),
        ("late", "متأخر"), ("unmarked", "لم يُسجّل بعد")
    )
    cards = "".join(
        f'<div class="pv2-attendance-stat pv2-attendance-stat--{key}">'
        f'<span>{label}</span><strong>{counts[key]}</strong></div>'
        for key, label in labels
    )
    return (
        '<div class="pv2-attendance-overview" aria-label="ملخص حضور الحصة">'
        '<div class="pv2-section-head"><h2>ملخص الحضور</h2>'
        f'<span>عدد المسجلين في الحصة: {len(enrollments)}</span></div>'
        + f'<div class="pv2-attendance-stats">{cards}</div>'
        '<p class="subtle">الأعداد مبنية على سجلات هذه الحصة فقط. '
        'الذين لم يُسجل حضورهم ليسوا غائبين تلقائياً.</p></div>'
    )


@app.get("/platform/sessions/{session_id}",response_class=HTMLResponse)
def session_page(session_id:int,request:Request,db:Session=Depends(get_db),user=Depends(actor)):
    s=db.get(SessionDay,session_id)
    if not s:raise HTTPException(404)
    slot=slot_scope(db,user,s.slot_id)
    enrollments=db.scalars(select(Enrollment).where(Enrollment.year_id==slot.year_id,
       Enrollment.center_id==slot.center_id,Enrollment.track_id==slot.track_id,Enrollment.status=="active")).all()
    marks={a.enrollment_id:a for a in db.scalars(select(Attendance).where(Attendance.session_id==s.id)).all()}
    report=db.scalar(select(SessionReport).where(SessionReport.session_id==s.id))
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
            if report and report.status=="approved":
                if old:
                    action=form(f"/platform/attendance/{old.id}/correction",request.session["csrf"],
                        choose("new_status","الحالة الجديدة",
                            [("present","حاضر"),("absent","غائب"),("late","متأخر")],
                            selected=old.status)+area("reason","سبب التصحيح المعلل"),"طلب تصحيح")
            else:
                action=attendance_choice_form(
                    s.id, en.id, request.session["csrf"],
                    old.status if old else None, bool(pend)
                )
        rows.append([f'<a href="/platform/learners/{en.id}">{e(l.full_name)}</a>',state,
           e(old.status if old else "لم يسجل"),action])
    summary = attendance_summary(enrollments, marks)
    body=(f'<section class="pv2-attendance-session"><h2>{e(slot.subject)} · {e(s.held_on)}</h2>'
          f'<p>الساعة {e(slot.starts_at)}–{e(slot.ends_at)} · الحالة: {e(s.status)}</p>'
          f'{summary}<h2>تسجيل حضور المستفيدين</h2>'
          f'{table(["المستفيد","التنبيه","الحضور الحالي","الإجراء"],rows)}</section>')
    if user["id"]==slot.staff_id and (not report or report.status in ("draft","returned")):
        body += session_report_editor(s.id, request.session["csrf"], report, slot.subject)
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
        raise HTTPException(422,"لا يجوز تسجيل الحضور أو الغياب لحصة ملغاة أو أثناء الإقصاء التأديبي")
    approved_report=db.scalar(select(SessionReport).where(
        SessionReport.session_id==s.id,SessionReport.status=="approved"))
    if approved_report:
        raise HTTPException(409,"الحضور مقفل بعد اعتماد تقرير الحصة؛ يتطلب تصحيحًا معللًا")
    pending_absences=db.scalar(select(func.count()).select_from(Attendance).where(
        Attendance.enrollment_id==en.id,Attendance.status=="absent",Attendance.permit_id.is_(None))) or 0
    if status in ("present","late") and pending_absences:
        raise HTTPException(409,"لا يجوز السماح بالدخول مع غياب معلق قبل إذن المنسق")
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

def report_status_summary(rows: list) -> str:
    """Counts correspond only to the reports already authorized and loaded."""
    labels = (
        ("submitted", "تنتظر المراجعة"),
        ("returned", "أُعيدت للتصحيح"),
        ("approved", "تمت المصادقة"),
        ("draft", "مسودات"),
    )
    counts = {key: 0 for key, _ in labels}
    for report in rows:
        if report.status in counts:
            counts[report.status] += 1
    cards = "".join(
        f'<div class="pv2-review-stat pv2-review-stat--{key}">'
        f'<span>{label}</span><strong>{counts[key]}</strong></div>'
        for key, label in labels
    )
    return (
        '<section class="pv2-review-overview"><div class="pv2-section-head">'
        '<h2>متابعة التقارير</h2><span>إحصائيات التقارير المعروضة وفق صلاحياتك</span>'
        '</div><div class="pv2-review-stats">' + cards + '</div>'
        '<p class="subtle">الأعداد مبنية على أحدث التقارير المحمّلة فقط، '
        'وقد لا تشمل جميع التقارير التاريخية.</p></section>'
    )


def report_filter_box(target: str, label: str) -> str:
    """Client-only filtering of already-authorized table rows."""
    return (
        '<div class="pv2-review-filter no-print">'
        f'<label for="filter-{target}">{e(label)}</label>'
        f'<input id="filter-{target}" type="search" data-table-filter="{target}" '
        'autocomplete="off" placeholder="اكتب كلمة للبحث في اللائحة...">'
        f'<span data-table-counter="{target}" role="status" aria-live="polite"></span>'
        '</div>'
    )


@app.get("/platform/reports", response_class=HTMLResponse)
def review_reports(request: Request, db: Session = Depends(get_db), user=Depends(actor)):
    csrf = request.session["csrf"]
    recent = db.scalars(select(SessionReport).order_by(SessionReport.id.desc()).limit(600)).all()
    visible = []
    rows = []
    sessions_by_id = by_id(db, SessionDay, (r.session_id for r in recent))
    slots_by_id = by_id(db, Slot, (item.slot_id for item in sessions_by_id.values()))
    for r in recent:
        session = sessions_by_id[r.session_id]
        slot = slots_by_id[session.slot_id]
        if not center_allowed(user, slot.center_id):
            continue
        if user["role"] in ("instructor", "educator") and r.author_id != user["id"]:
            continue
        visible.append(r)
        actions = []
        if user["role"] == "coordinator" and r.status == "submitted":
            actions.append(
                form(f"/platform/reports/{r.id}/review", csrf,
                     '<input type="hidden" name="decision" value="approved">', "مصادقة")
            )
            actions.append(
                '<details class="pv2-review-disclosure"><summary>إرجاع للتصحيح</summary>'
                + form(f"/platform/reports/{r.id}/review", csrf,
                       '<input type="hidden" name="decision" value="returned">'
                       + '<label>سبب الإرجاع'
                       '<textarea name="comment" required minlength="5" rows="3"></textarea></label>',
                       "تأكيد الإرجاع") + '</details>'
            )
        actions.append(
            f'<a class="button" href="/platform/reports/{r.id}/attachments">المرفقات</a>'
        )
        if r.status == "approved":
            actions.append(
                form(f"/platform/reports/{r.id}/print", csrf, "",
                     "تقرير رسمي مع رمز تحقق")
            )
        if user["role"] == "admin":
            actions.append(
                '<details class="pv2-review-disclosure pv2-review-danger">'
                '<summary>خيارات حذف التقرير</summary>'
                + form(f"/platform/reports/{r.id}/erase", csrf,
                       area("reason", "السبب المكتوب للحذف النهائي") +
                       field("confirmation", "اكتب: حذف التقرير"),
                       "حذف التقرير نهائيًا")
                + '</details>'
            )
        status_key = r.status if r.status in ("submitted", "approved", "returned", "draft") else "other"
        status = (
            f'<span class="pv2-status pv2-status--{status_key}">'
            f'{e(STATUS_LABELS.get(r.status, r.status))}</span>'
        )
        rows.append([
            e(session.held_on), e(slot.subject), e(r.subject), status,
            e(r.review_comment) or "—",
            '<div class="pv2-review-actions">' + "".join(actions) + '</div>',
        ])
    body = report_status_summary(visible)
    body += (
        '<section class="pv2-review-list"><div class="pv2-section-head">'
        '<h2>تقارير الحصص</h2><span>الاطلاع والمراجعة حسب صلاحيات الحساب</span></div>'
        + report_filter_box("session-reports-table", "بحث داخل التقارير المعروضة")
        + '<div id="session-reports-table">'
        + table(["التاريخ", "المادة", "عنوان التقرير", "الحالة", "ملاحظة المنسق", "الإجراءات"], rows)
        + '</div></section>'
    )
    if user["role"] in ("admin", "coordinator"):
        summaries = db.scalars(select(CenterReport).order_by(CenterReport.id.desc()).limit(200)).all()
        summaryrows = []
        summary_centers = by_id(db, Center, (item.center_id for item in summaries))
        for item in summaries:
            if not center_allowed(user, item.center_id):
                continue
            actions = []
            if user["role"] == "coordinator" and item.author_id == user["id"] and item.status in ("draft", "returned"):
                actions.append(form(f"/platform/summary/{item.id}/submit", csrf, "", "إرسال للإدارة"))
            if user["role"] == "admin" and item.status == "submitted":
                actions.append(form(
                    f"/platform/summary/{item.id}/review", csrf,
                    '<input name="decision" type="hidden" value="approved">', "مصادقة"
                ))
                actions.append(
                    '<details class="pv2-review-disclosure"><summary>إرجاع للتصحيح</summary>'
                    + form(f"/platform/summary/{item.id}/review", csrf,
                           '<input name="decision" type="hidden" value="returned">'
                           '<label>سبب الإرجاع'
                           '<textarea name="comment" required minlength="5" rows="3"></textarea></label>',
                           "تأكيد الإرجاع") + '</details>'
                )
            summaryrows.append([
                e(summary_centers[item.center_id].name), e(item.period_type),
                e(item.period_start), e(item.period_end),
                e(STATUS_LABELS.get(item.status, item.status)),
                '<div class="pv2-review-actions">' + "".join(actions) + '</div>' if actions else "—",
            ])
        body += (
            '<section class="pv2-review-list"><div class="pv2-section-head">'
            '<h2>تقارير المنسقين</h2><span>مراجعة إدارة الجمعية</span></div>'
            + report_filter_box("center-reports-table", "بحث داخل تقارير المنسقين")
            + '<div id="center-reports-table">'
            + table(["المركز", "المدة", "من", "إلى", "الحالة", "الإجراء"], summaryrows)
            + '</div></section>'
        )
        if user["role"] == "coordinator":
            years = db.scalars(select(Year)).all()
            body += (
                '<section class="pv2-review-create"><details>'
                '<summary>إنشاء تقرير دوري جديد للمنسق</summary>'
                '<p class="subtle">حدد السنة والمركز والفترة، ثم أضف التحليل والتوصيات.</p>'
                + form("/platform/summary", csrf,
                       choose("year_id", "السنة", [(y.id, y.label) for y in years]) +
                       choose("center_id", "المركز", center_options(db, user)) +
                       choose("period_type", "التواتر", [
                           ("weekly", "أسبوعي"), ("monthly", "شهري"),
                           ("activity", "نشاط"), ("annual", "سنوي"),
                       ]) +
                       field("period_start", "من", "date") +
                       field("period_end", "إلى", "date") +
                       area("analysis", "التحليل والتوصيات"), "حفظ التقرير")
                + '</details></section>'
            )
    return page(request, user, "التقارير والمصادقة", body)


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
@media print{{button{{display:none}}}}</style></head><body><header><img src="/static/noor_official_logo.webp" alt="شعار جمعية نور الأمل" width="115" height="102"><h2>جمعية نور الأمل</h2>
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
