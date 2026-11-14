"""Noor Al Amal - Second Chance reporting portal (FastAPI / SQLAlchemy)."""
from __future__ import annotations

import csv
import io
import os
import secrets
import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Date, DateTime, Integer, MetaData, String, Text, create_engine, func, inspect, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
IS_VERCEL = bool(os.getenv('VERCEL'))
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL and not IS_VERCEL:
    DATABASE_URL = f"sqlite:///{ROOT / 'noor_reports.db'}"
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = 'postgresql://' + DATABASE_URL[len('postgres://'):]
PG = bool(DATABASE_URL and DATABASE_URL.startswith('postgresql'))
DB_SCHEMA = 'noor_app' if PG else None
APP_SECRET = os.getenv('APP_SECRET') or (secrets.token_urlsafe(40) if not IS_VERCEL else '')
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD_HASH = os.getenv('ADMIN_PASSWORD_HASH', '')
READY = bool(DATABASE_URL and APP_SECRET and ADMIN_PASSWORD_HASH)

class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)

class Report(Base):
    __tablename__ = 'reports'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    group_name: Mapped[str] = mapped_column(String(120), nullable=False)
    trainer_name: Mapped[str] = mapped_column(String(140), nullable=False)
    venue: Mapped[str] = mapped_column(String(200), default='')
    start_time: Mapped[str] = mapped_column(String(10), default='')
    duration_minutes: Mapped[int] = mapped_column(Integer, default=90)
    present_count: Mapped[int] = mapped_column(Integer, default=0)
    absent_count: Mapped[int] = mapped_column(Integer, default=0)
    objectives: Mapped[str] = mapped_column(Text, default='')
    activities: Mapped[str] = mapped_column(Text, default='')
    results: Mapped[str] = mapped_column(Text, default='')
    challenges: Mapped[str] = mapped_column(Text, default='')
    recommendations: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

class Absence(Base):
    __tablename__ = 'absences'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    absence_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    learner_name: Mapped[str] = mapped_column(String(140), nullable=False)
    group_name: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(String(60), default='غير محدد')
    justified: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

engine = None
if DATABASE_URL:
    opts = {'pool_pre_ping': True}
    if PG:
        ssl_mode = os.getenv('NOOR_DB_SSLMODE', 'require')
        if ssl_mode not in ('require', 'verify-ca', 'verify-full', 'disable'):
            raise RuntimeError('Invalid database TLS mode')
        if IS_VERCEL and ssl_mode == 'disable':
            raise RuntimeError('Database TLS must not be disabled on Vercel')
        opts.update(poolclass=NullPool,
                    connect_args={'connect_timeout': 12, 'sslmode': ssl_mode})
    else:
        opts.update(connect_args={'check_same_thread': False})
    engine = create_engine(DATABASE_URL, **opts)
_db_ready = False

def initialize_database():
    global _db_ready
    if _db_ready or engine is None:
        return
    # Production schemas are changed only by reviewed migrations. Runtime
    # requests must never create private learner tables without RLS protection.
    if PG:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
    else:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
    _db_ready = True

def get_db():
    if engine is None:
        raise HTTPException(status_code=503, detail='Database is not configured')
    initialize_database()
    with Session(engine) as session:
        yield session

def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False

def auth_required(request: Request):
    if not request.session.get('authenticated'):
        raise HTTPException(status_code=303, headers={'Location': '/login'}, detail='Authentication required')
    # The legacy report/absence endpoints are intentionally restricted to admin.
    if request.session.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='This page is restricted to administration')

def logged_in_required(request: Request):
    if not request.session.get('authenticated'):
        raise HTTPException(status_code=303, headers={'Location': '/login'}, detail='Authentication required')

def check_csrf(request: Request, token: str):
    if not token or not hmac.compare_digest(token, request.session.get('csrf', '')):
        raise HTTPException(status_code=403, detail='Invalid form security token')

KIND_LABELS = {'training': 'التكوين المهني', 'pedagogical': 'التأهيل التربوي', 'activity': 'الأنشطة الموازية'}
REASON_LABELS = ('غير محدد', 'مرض', 'ظروف عائلية', 'انقطاع', 'تأخر', 'أخرى')
app = FastAPI(title='نور الأمل | تقارير الفرصة الثانية', docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET or secrets.token_urlsafe(40), same_site='lax', https_only=IS_VERCEL or os.getenv('APP_FORCE_HTTPS') == '1', max_age=60*60*8)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Jinja2Templates(directory=str(ROOT / 'templates'))
templates.env.globals['kind_labels'] = KIND_LABELS
templates.env.globals['today'] = date.today

def render(request: Request, template: str, **context):
    return templates.TemplateResponse(request=request, name=template, context={
        'request': request, 'csrf': request.session.get('csrf', ''),
        'user': ADMIN_USERNAME, 'page': context.pop('page', ''), **context,
    })

@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store' if request.url.path != '/static/style.css' else 'public, max-age=86400'
    return response

@app.get('/health')
def health():
    if not READY:
        return {'service': 'noor-alamal', 'status': 'setup-required'}
    try:
        initialize_database()
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
            # Release readiness requires all tables, not just an open connection.
            # This detects a missing v2 migration before any real user logs in.
            names = [table.name for table in Base.metadata.tables.values()]
            inspection = inspect(conn)
            missing = [name for name in names if not inspection.has_table(name, schema=DB_SCHEMA)]
            if missing:
                return JSONResponse(
                    {'service': 'noor-alamal', 'status': 'migration-required'},
                    status_code=503,
                )
    except (SQLAlchemyError, OSError):
        return JSONResponse({'service': 'noor-alamal', 'status': 'database-unavailable'}, status_code=503)
    return {'service': 'noor-alamal', 'status': 'ready'}

@app.get('/setup', response_class=HTMLResponse)
def setup(request: Request):
    if READY:
        return RedirectResponse('/login', status_code=303)
    return render(request, 'setup.html', page='setup')

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    if not READY:
        return RedirectResponse('/setup', status_code=303)
    if request.session.get('authenticated'):
        return RedirectResponse('/platform', status_code=303)
    return render(request, 'login.html', page='login')

@app.post('/login')
def login(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()],
          db: Session = Depends(get_db)):
    if not READY:
        return RedirectResponse('/setup', status_code=303)
    from platform_v2_models import LoginAttempt, now
    # Only keyed pseudonymous fingerprints are persisted, never raw source IP
    # addresses or entered usernames. A separate bucket is kept per peer+name.
    peer = request.client.host if request.client else "unknown"
    fingerprint = hmac.new(APP_SECRET.encode("utf-8"),
        (peer + "\\0" + username.strip().casefold()).encode("utf-8"),
        hashlib.sha256).hexdigest()
    moment = now()
    attempt = db.get(LoginAttempt, fingerprint)
    if attempt and attempt.locked_until and attempt.locked_until > moment:
        return render(request, 'login.html', page='login',
                      error='تعذّر تسجيل الدخول مؤقتًا. حاول بعد عدة دقائق.')
    is_admin = (hmac.compare_digest(username, ADMIN_USERNAME) and
                verify_password(password, ADMIN_PASSWORD_HASH))
    staff = None
    if not is_admin:
        from platform_v2 import authenticate_staff
        staff = authenticate_staff(db, username, password)
    if not is_admin and staff is None:
        if not attempt:
            attempt = LoginAttempt(key_hash=fingerprint, failures=0, first_at=moment)
            db.add(attempt)
        if moment - attempt.first_at > timedelta(minutes=15):
            attempt.failures = 0
            attempt.first_at = moment
        attempt.failures += 1
        if attempt.failures >= 5:
            attempt.locked_until = moment + timedelta(minutes=10)
        db.commit()
        return render(request, 'login.html', page='login', error='اسم المستخدم أو كلمة المرور غير صحيحين.')
    if attempt:
        db.delete(attempt)
        db.commit()
    request.session.clear()
    request.session['authenticated'] = True
    request.session['csrf'] = secrets.token_urlsafe(32)
    request.session['role'] = 'admin' if is_admin else staff.role
    if staff:
        request.session['staff_id'] = staff.id
        request.session['session_version'] = staff.session_version
    return RedirectResponse('/platform', status_code=303)

@app.post('/logout')
def logout(request: Request, token: Annotated[str, Form(alias='_csrf')], _: None = Depends(logged_in_required)):
    check_csrf(request, token)
    request.session.clear()
    return RedirectResponse('/login', status_code=303)

@app.get('/')
def main_entry(request: Request, _: None = Depends(logged_in_required)):
    # Operational dashboard is the primary app for all authenticated roles.
    # The previous reports interface remains available at /legacy.
    return RedirectResponse('/platform', status_code=303)

@app.get('/legacy', response_class=HTMLResponse)
def dashboard(request: Request, _: None = Depends(auth_required), db: Session = Depends(get_db)):
    counts = {kind: db.scalar(select(func.count()).select_from(Report).where(Report.kind == kind)) or 0 for kind in KIND_LABELS}
    total = sum(counts.values())
    absences = db.scalar(select(func.count()).select_from(Absence)) or 0
    month_start = date.today().replace(day=1)
    this_month = db.scalar(select(func.count()).select_from(Report).where(Report.session_date >= month_start)) or 0
    present = db.scalar(select(func.coalesce(func.sum(Report.present_count), 0))) or 0
    absent = db.scalar(select(func.coalesce(func.sum(Report.absent_count), 0))) or 0
    attendance_rate = round(100 * present / (present + absent)) if (present + absent) else None
    recent = db.scalars(select(Report).order_by(Report.session_date.desc(), Report.id.desc()).limit(6)).all()
    return render(request, 'dashboard.html', page='dashboard', counts=counts, total=total, absences=absences,
                  this_month=this_month, attendance_rate=attendance_rate, recent=recent)

@app.get('/reports', response_class=HTMLResponse)
def reports_list(request: Request, kind: str = '', q: str = '', _: None = Depends(auth_required), db: Session = Depends(get_db)):
    stmt = select(Report)
    if kind in KIND_LABELS:
        stmt = stmt.where(Report.kind == kind)
    if q.strip():
        pattern = '%' + q.strip()[:100] + '%'
        stmt = stmt.where(or_(Report.subject.ilike(pattern), Report.group_name.ilike(pattern), Report.trainer_name.ilike(pattern)))
    rows = db.scalars(stmt.order_by(Report.session_date.desc(), Report.id.desc()).limit(500)).all()
    return render(request, 'reports.html', page='reports', rows=rows, selected_kind=kind, q=q)

@app.get('/reports/new', response_class=HTMLResponse)
def report_new(request: Request, kind: str = 'training', _: None = Depends(auth_required)):
    if kind not in KIND_LABELS: kind = 'training'
    return render(request, 'report_form.html', page='reports', report=None, selected_kind=kind, title='إضافة تقرير جديد', action='/reports')

def validate_report(kind, session_date, subject, group_name, trainer_name, duration_minutes, present_count, absent_count):
    if kind not in KIND_LABELS:
        raise HTTPException(422, detail='Invalid report type')
    if not all(x.strip() for x in (subject, group_name, trainer_name)):
        raise HTTPException(422, detail='Required fields are missing')
    if any(n < 0 for n in (duration_minutes, present_count, absent_count)) or duration_minutes > 1440:
        raise HTTPException(422, detail='Invalid duration or count')
    return session_date

# Shared form-body dependency for create/edit.
def report_fields(
    kind: Annotated[str, Form()], session_date: Annotated[date, Form()], subject: Annotated[str, Form()],
    group_name: Annotated[str, Form()], trainer_name: Annotated[str, Form()],
    venue: Annotated[str, Form()] = '', start_time: Annotated[str, Form()] = '',
    duration_minutes: Annotated[int, Form()] = 90, present_count: Annotated[int, Form()] = 0,
    absent_count: Annotated[int, Form()] = 0, objectives: Annotated[str, Form()] = '',
    activities: Annotated[str, Form()] = '', results: Annotated[str, Form()] = '',
    challenges: Annotated[str, Form()] = '', recommendations: Annotated[str, Form()] = '',
):
    validate_report(kind, session_date, subject, group_name, trainer_name, duration_minutes, present_count, absent_count)
    return dict(kind=kind, session_date=session_date, subject=subject[:200].strip(), group_name=group_name[:120].strip(),
        trainer_name=trainer_name[:140].strip(), venue=venue[:200].strip(), start_time=start_time[:10],
        duration_minutes=duration_minutes, present_count=present_count, absent_count=absent_count,
        objectives=objectives[:12000], activities=activities[:12000], results=results[:12000],
        challenges=challenges[:12000], recommendations=recommendations[:12000])

@app.post('/reports')
def report_create(request: Request, token: Annotated[str, Form(alias='_csrf')], fields: dict = Depends(report_fields),
                  _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    obj = Report(**fields)
    db.add(obj); db.commit(); db.refresh(obj)
    return RedirectResponse(f'/reports/{obj.id}?saved=1', status_code=303)

@app.get('/reports/{report_id}', response_class=HTMLResponse)
def report_detail(request: Request, report_id: int, saved: int = 0, _: None = Depends(auth_required), db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None: raise HTTPException(404, 'Report not found')
    return render(request, 'report_detail.html', page='reports', report=report, saved=bool(saved))

@app.get('/reports/{report_id}/edit', response_class=HTMLResponse)
def report_edit_page(request: Request, report_id: int, _: None = Depends(auth_required), db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None: raise HTTPException(404, 'Report not found')
    return render(request, 'report_form.html', page='reports', report=report, selected_kind=report.kind,
                  title='تعديل التقرير', action=f'/reports/{report_id}/edit')

@app.post('/reports/{report_id}/edit')
def report_edit(request: Request, report_id: int, token: Annotated[str, Form(alias='_csrf')],
                fields: dict = Depends(report_fields), _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    report = db.get(Report, report_id)
    if report is None: raise HTTPException(404, 'Report not found')
    for key, val in fields.items(): setattr(report, key, val)
    db.commit()
    return RedirectResponse(f'/reports/{report_id}?saved=1', status_code=303)

@app.post('/reports/{report_id}/delete')
def report_delete(request: Request, report_id: int, token: Annotated[str, Form(alias='_csrf')],
                  reason: Annotated[str, Form()], confirmation: Annotated[str, Form()],
                  _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    if confirmation.strip() != 'حذف التقرير' or len(reason.strip()) < 10:
        raise HTTPException(422, 'تأكيد الحذف النهائي وتعليله إلزاميان')
    report = db.get(Report, report_id)
    if report is None: raise HTTPException(404, 'Report not found')
    from platform_v2_models import Audit
    proof = hmac.new(APP_SECRET.encode(), reason.strip().encode(), hashlib.sha256).hexdigest()[:16]
    db.delete(report)
    db.add(Audit(actor=ADMIN_USERNAME, action='legacy_report_erasure',
                 resource='report', resource_id=report_id,
                 reason='reason_digest=' + proof))
    db.commit()
    return RedirectResponse('/reports', status_code=303)

@app.get('/absences', response_class=HTMLResponse)
def absences_list(request: Request, q: str = '', _: None = Depends(auth_required), db: Session = Depends(get_db)):
    stmt = select(Absence)
    if q.strip():
        pattern = '%' + q.strip()[:100] + '%'
        stmt = stmt.where(or_(Absence.learner_name.ilike(pattern), Absence.group_name.ilike(pattern)))
    rows = db.scalars(stmt.order_by(Absence.absence_date.desc(), Absence.id.desc()).limit(500)).all()
    total = db.scalar(select(func.count()).select_from(Absence)) or 0
    unjustified = db.scalar(select(func.count()).select_from(Absence).where(Absence.justified.is_(False))) or 0
    return render(request, 'absences.html', page='absences', rows=rows, q=q, total=total, unjustified=unjustified)

@app.get('/absences/new', response_class=HTMLResponse)
def absence_new(request: Request, _: None = Depends(auth_required)):
    return render(request, 'absence_form.html', page='absences', absence=None, reason_labels=REASON_LABELS,
                  title='تسجيل غياب جديد', action='/absences')

def absence_fields(absence_date: Annotated[date, Form()], learner_name: Annotated[str, Form()],
                   group_name: Annotated[str, Form()], reason: Annotated[str, Form()] = 'غير محدد',
                   justified: Annotated[bool, Form()] = False, notes: Annotated[str, Form()] = ''):
    if not learner_name.strip() or not group_name.strip():
        raise HTTPException(422, 'Required fields are missing')
    return dict(absence_date=absence_date, learner_name=learner_name[:140].strip(),
                group_name=group_name[:120].strip(), reason=reason[:60], justified=justified, notes=notes[:6000])

@app.post('/absences')
def absence_create(request: Request, token: Annotated[str, Form(alias='_csrf')], fields: dict = Depends(absence_fields),
                   _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    db.add(Absence(**fields)); db.commit()
    return RedirectResponse('/absences?saved=1', status_code=303)

@app.get('/absences/{absence_id:int}/edit', response_class=HTMLResponse)
def absence_edit_page(request: Request, absence_id: int, _: None = Depends(auth_required), db: Session = Depends(get_db)):
    absence = db.get(Absence, absence_id)
    if absence is None: raise HTTPException(404, 'Absence not found')
    return render(request, 'absence_form.html', page='absences', absence=absence, reason_labels=REASON_LABELS,
                  title='تعديل سجل الغياب', action=f'/absences/{absence_id:int}/edit')

@app.post('/absences/{absence_id:int}/edit')
def absence_edit(request: Request, absence_id: int, token: Annotated[str, Form(alias='_csrf')],
                 fields: dict = Depends(absence_fields), _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    absence = db.get(Absence, absence_id)
    if absence is None: raise HTTPException(404, 'Absence not found')
    for key, value in fields.items(): setattr(absence, key, value)
    db.commit()
    return RedirectResponse('/absences?saved=1', status_code=303)

@app.post('/absences/{absence_id:int}/delete')
def absence_delete(request: Request, absence_id: int, token: Annotated[str, Form(alias='_csrf')],
                   _: None = Depends(auth_required), db: Session = Depends(get_db)):
    check_csrf(request, token)
    absence = db.get(Absence, absence_id)
    if absence is None: raise HTTPException(404, 'Absence not found')
    db.delete(absence); db.commit()
    return RedirectResponse('/absences', status_code=303)

@app.get('/absences/summary/print', response_class=HTMLResponse)
def absence_summary(request: Request, start: date | None = None, end: date | None = None, group: str = '',
                    _: None = Depends(auth_required), db: Session = Depends(get_db)):
    stmt = select(Absence)
    if start: stmt = stmt.where(Absence.absence_date >= start)
    if end: stmt = stmt.where(Absence.absence_date <= end)
    if group.strip(): stmt = stmt.where(Absence.group_name.ilike('%' + group[:100] + '%'))
    rows = db.scalars(stmt.order_by(Absence.absence_date.desc(), Absence.id.desc()).limit(2000)).all()
    return render(request, 'absence_summary.html', page='absences', rows=rows, start=start, end=end,
                  group=group, justified=sum(a.justified for a in rows), unjustified=sum(not a.justified for a in rows))

def safe_csv(value):
    """Neutralize spreadsheet formulas while preserving Arabic and UTF-8."""
    raw = str(value if value is not None else '')
    if raw.lstrip().startswith(('=', '+', '-', '@')) or raw[:1] in ('\t', '\r', '\n'):
        raw = "'" + raw
    return raw

def make_csv(filename: str, headers: list[str], rows: list[list[object]]):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows: writer.writerow([safe_csv(v) for v in row])
    content = '\ufeff' + output.getvalue()
    return StreamingResponse(iter([content.encode('utf-8')]), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename="{filename}"'})

@app.get('/export/reports.csv')
def export_reports(_: None = Depends(auth_required), db: Session = Depends(get_db)):
    items = db.scalars(select(Report).order_by(Report.session_date.desc()).limit(20000)).all()
    return make_csv('noor-reports.csv', ['الرقم', 'النوع', 'التاريخ', 'عنوان الحصة', 'المجموعة', 'المؤطر', 'المدة بالدقائق', 'الحاضرون', 'الغائبون', 'الأهداف', 'الأنشطة', 'النتائج', 'الصعوبات', 'التوصيات'], [
        [r.id, KIND_LABELS[r.kind], r.session_date, r.subject, r.group_name, r.trainer_name, r.duration_minutes,
         r.present_count, r.absent_count, r.objectives, r.activities, r.results, r.challenges, r.recommendations] for r in items])

@app.get('/export/absences.csv')
def export_absences(_: None = Depends(auth_required), db: Session = Depends(get_db)):
    items = db.scalars(select(Absence).order_by(Absence.absence_date.desc()).limit(20000)).all()
    return make_csv('noor-absences.csv', ['الرقم', 'التاريخ', 'اسم المستفيد', 'المجموعة', 'سبب الغياب', 'الحالة', 'ملاحظات'], [
        [a.id, a.absence_date, a.learner_name, a.group_name, a.reason, 'مبرر' if a.justified else 'غير مبرر', a.notes] for a in items])

# Load the role-restricted second-generation portal after core models and routes.
import platform_v2  # noqa: E402,F401
import platform_v2_imports  # noqa: E402,F401
import platform_v2_exports  # noqa: E402,F401
import platform_v2_aggregates  # noqa: E402,F401
import platform_v2_annual_report  # noqa: E402,F401
import platform_v2_auto_summaries  # noqa: E402,F401
import platform_v2_attachments  # noqa: E402,F401
import platform_v2_activity_album  # noqa: E402,F401
import platform_v2_attendance_corrections  # noqa: E402,F401
import platform_v2_staff_profiles  # noqa: E402,F401
import platform_v2_learner_cards  # noqa: E402,F401
import platform_v2_learner_progress  # noqa: E402,F401
import platform_v2_skills  # noqa: E402,F401
import platform_v2_integration  # noqa: E402,F401
import platform_v2_learner_completeness  # noqa: E402,F401
import platform_v2_privacy  # noqa: E402,F401
import platform_v2_schedule_exceptions  # noqa: E402,F401
import platform_v2_timetable_print  # noqa: E402,F401
import platform_v2_schedule_proposals  # noqa: E402,F401
import platform_v2_learner_transfers  # noqa: E402,F401
import platform_v2_report_print  # noqa: E402,F401
import platform_v2_attendance_stats  # noqa: E402,F401
