"""Operational data model for Noor Al Amal, kept separate from legacy reports.
No personal data or credentials are placed in client-side code.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app import Base, DB_SCHEMA

def fk(table: str) -> str:
    return f"{DB_SCHEMA}.{table}.id" if DB_SCHEMA else f"{table}.id"

def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

class Center(Base):
    __tablename__ = "v2_centers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(140), unique=True)
    area: Mapped[str] = mapped_column(String(100))

class Year(Base):
    __tablename__ = "v2_years"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(40), unique=True)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

class Staff(Base):
    __tablename__ = "v2_staff"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    full_name: Mapped[str] = mapped_column(String(140))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))  # coordinator/instructor/educator
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Track(Base):
    __tablename__ = "v2_tracks"
    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    year_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_years")))
    name: Mapped[str] = mapped_column(String(140))
    __table_args__ = (UniqueConstraint("year_id", "center_id", "name", name="uq_v2_track"),)

class Assignment(Base):
    __tablename__ = "v2_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    track_id: Mapped[int | None] = mapped_column(ForeignKey(fk("v2_tracks")), nullable=True)
    subject: Mapped[str] = mapped_column(String(90), default="")
    __table_args__ = (UniqueConstraint("staff_id", "center_id", "track_id", "subject", name="uq_v2_assignment"),)

class Learner(Base):
    __tablename__ = "v2_learners"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(180))
    gender: Mapped[str] = mapped_column(String(20), default="")
    born_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    birthplace: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(45), default="")
    address: Mapped[str] = mapped_column(String(240), default="")
    guardian_name: Mapped[str] = mapped_column(String(180), default="")
    guardian_relationship: Mapped[str] = mapped_column(String(70), default="")
    guardian_phone: Mapped[str] = mapped_column(String(45), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Enrollment(Base):
    __tablename__ = "v2_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_learners")))
    year_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_years")))
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    track_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_tracks")))
    enrolled_on: Mapped[date] = mapped_column(Date, default=date.today)
    status: Mapped[str] = mapped_column(String(24), default="active")
    __table_args__ = (UniqueConstraint("learner_id", "year_id", name="uq_v2_enrollment_year"),)

class Slot(Base):
    __tablename__ = "v2_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    year_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_years")))
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    track_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_tracks")))
    staff_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    subject: Mapped[str] = mapped_column(String(90))
    kind: Mapped[str] = mapped_column(String(25))
    weekday: Mapped[int] = mapped_column(Integer)  # Monday = 0
    starts_at: Mapped[str] = mapped_column(String(5))
    ends_at: Mapped[str] = mapped_column(String(5))
    specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    canceled: Mapped[bool] = mapped_column(Boolean, default=False)
    change_reason: Mapped[str] = mapped_column(Text, default="")

class Closure(Base):
    __tablename__ = "v2_closures"
    id: Mapped[int] = mapped_column(primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    closed_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(140))
    __table_args__ = (UniqueConstraint("center_id", "closed_on", name="uq_v2_closure"),)

class SessionDay(Base):
    __tablename__ = "v2_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_slots")))
    held_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    __table_args__ = (UniqueConstraint("slot_id", "held_on", name="uq_v2_session"),)

class Permit(Base):
    __tablename__ = "v2_permits"
    id: Mapped[int] = mapped_column(primary_key=True)
    learner_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_learners")))
    center_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_centers")))
    reason: Mapped[str] = mapped_column(Text)
    issued_by: Mapped[str] = mapped_column(String(140))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Attendance(Base):
    __tablename__ = "v2_attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_sessions")))
    enrollment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_enrollments")))
    status: Mapped[str] = mapped_column(String(20))
    permit_id: Mapped[int | None] = mapped_column(ForeignKey(fk("v2_permits")), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(140))
    __table_args__ = (UniqueConstraint("session_id", "enrollment_id", name="uq_v2_attendance"),)

class SessionReport(Base):
    __tablename__ = "v2_session_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_sessions")), unique=True)
    author_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    subject: Mapped[str] = mapped_column(String(200))
    objectives: Mapped[str] = mapped_column(Text, default="")
    activities: Mapped[str] = mapped_column(Text, default="")
    results: Mapped[str] = mapped_column(Text, default="")
    challenges: Mapped[str] = mapped_column(Text, default="")
    recommendations: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    review_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(140), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Assessment(Base):
    __tablename__ = "v2_assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_tracks")))
    staff_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    subject: Mapped[str] = mapped_column(String(90))
    title: Mapped[str] = mapped_column(String(180))
    held_on: Mapped[date] = mapped_column(Date)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(7,2))

class Grade(Base):
    __tablename__ = "v2_grades"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_assessments")))
    enrollment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_enrollments")))
    score: Mapped[Decimal] = mapped_column(Numeric(5,2))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    correction_reason: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str] = mapped_column(String(140), default="")
    __table_args__ = (UniqueConstraint("assessment_id", "enrollment_id", name="uq_v2_grade"),)

class Incident(Base):
    __tablename__ = "v2_incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_enrollments")))
    staff_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    educational_action: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    review_comment: Mapped[str] = mapped_column(Text, default="")

class Discipline(Base):
    __tablename__ = "v2_discipline"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_enrollments")))
    decision: Mapped[str] = mapped_column(Text)
    minutes: Mapped[str] = mapped_column(Text)
    excluded_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    excluded_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(26), default="pending_admin")
    approved_by: Mapped[str] = mapped_column(String(140), default="")
    early_lift_reason: Mapped[str] = mapped_column(Text, default="")
    return_authorized: Mapped[bool] = mapped_column(Boolean, default=False)

class Notice(Base):
    __tablename__ = "v2_notices"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_enrollments")))
    kind: Mapped[str] = mapped_column(String(30))
    period_key: Mapped[str] = mapped_column(String(36), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[date | None] = mapped_column(Date, nullable=True)

class Notification(Base):
    __tablename__ = "v2_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey(fk("v2_staff")))
    message: Mapped[str] = mapped_column(String(300))
    target: Mapped[str] = mapped_column(String(255), default="/platform")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Audit(Base):
    __tablename__ = "v2_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(140))
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
