"""Printable, access-scoped visual evidence for center periodic reports.

Figures are calculated from approved sessions only. Images are limited to
explicitly selected attachments from those approved session reports.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_v2 import e
from platform_v2_aggregates import approved_stats
from platform_v2_models import Attachment, Attendance, Slot


def visual_report_sections(db: Session, report) -> str:
    from platform_v2_auto_summaries import approved_evidence

    evidence = approved_evidence(
        db, report.center_id, report.year_id,
        report.period_start, report.period_end,
    )
    slots = db.scalars(select(Slot).where(
        Slot.center_id == report.center_id,
        Slot.year_id == report.year_id,
    )).all()
    stats = approved_stats(db, slots, report.period_start, report.period_end)
    total_marks = stats["present"] + stats["absent"]
    percent = round(stats["present"] * 100 / total_marks, 1) if total_marks else None

    indicators = [
        ("الحصص المعتمدة", stats["sessions"]),
        ("الحصص المسجلة الحضور", stats["recorded"]),
        ("حالات الحضور والتأخر", stats["present"]),
        ("حالات الغياب", stats["absent"]),
        ("أيام الغياب بحسب المستفيد", stats["absent_days"]),
        ("حصص دون سجل حضور", stats["missing"]),
    ]
    cards = "".join(
        f'<div class="indicator"><span>{e(title)}</span><strong>{value}</strong></div>'
        for title, value in indicators
    )

    # No misleading percentage if attendance was not recorded.
    if percent is None:
        graph = (
            '<p class="no-data">لا يمكن حساب نسبة الحضور دون سجلات '
            'حضور أو غياب معتمدة.</p>'
        )
    else:
        absence_percent = round(100 - percent, 1)
        graph = (
            f'<div class="chart" role="img" aria-label="الحضور والتأخر {percent}% '
            f'والغياب {absence_percent}% من سجلات الحضور والغياب">'
            f'<div class="bar present" style="width:{percent}%"></div>'
            f'<div class="bar absent" style="width:{absence_percent}%"></div>'
            '</div>'
            f'<p class="chart-key">الحضور والتأخر: {stats["present"]} ({percent}%) '
            f'· الغياب: {stats["absent"]} ({absence_percent}%)</p>'
        )

    per_track = Counter(track.name for _, _, _, track in evidence)
    max_sessions = max(per_track.values(), default=0)
    track_chart = "".join(
        '<div class="track-bar-row">'
        f'<span>{e(name)}</span>'
        '<div class="track-bar-bg" role="img" '
        f'aria-label="{e(name)}: {count} حصة معتمدة">'
        f'<div class="track-bar" style="width:{round(count * 100 / max_sessions, 1)}%"></div>'
        '</div>'
        f'<strong>{count}</strong></div>'
        for name, count in sorted(per_track.items())
    ) or '<p class="no-data">لا توجد حصص معتمدة للمقارنة بين المسالك.</p>'

    activity_rows = "".join(
        '<tr>'
        f'<td>{e(session.held_on.isoformat())}</td>'
        f'<td>{e(track.name)}</td>'
        f'<td>{e(slot.subject)}</td>'
        f'<td>{e(item.activities.strip()) if item.activities.strip() else "—"}</td>'
        f'<td>#{item.id}</td>'
        '</tr>'
        for session, item, slot, track in evidence
    ) or '<tr><td colspan="5">لا توجد حصص مصادق عليها في هذه الفترة.</td></tr>'

    # Attachments are private. Never use unselected images, nor attachments
    # from another center, year, period, canceled session, or draft report.
    source_ids = {item.id for _, item, _, _ in evidence}
    images = db.scalars(select(Attachment).where(
        Attachment.report_id.in_(source_ids),
        Attachment.select_for_summary.is_(True),
        Attachment.content_type.in_(("image/png", "image/jpeg")),
    ).order_by(Attachment.id).limit(8)).all() if source_ids else []
    gallery = "".join(
        '<figure>'
        f'<img src="/platform/attachments/{item.id}/view" '
        f'alt="{e(item.original_name)}" loading="eager">'
        f'<figcaption>{e(item.original_name)} · تقرير الحصة #{item.report_id}</figcaption>'
        '</figure>'
        for item in images
    )
    if not gallery:
        gallery = (
            '<p class="no-data">لا توجد صور مختارة للإدراج. '
            'يمكن للمنسق اختيار الصور من مرفقات تقارير الحصص المعتمدة.</p>'
        )

    return "".join([
        '<section class="visual-section">',
        '<h2>مؤشرات الفترة</h2>',
        '<div class="indicator-grid">', cards, '</div>',
        '<h3>مبيان توزيع سجلات الحضور والغياب</h3>', graph,
        '<p class="note">تحسب النسبة من سجلات الحضور والغياب المدخلة '
        'داخل الحصص المعتمدة، ولا تمثل نسبة المستفيدين الفريدين.</p>',
        '<h3>عدد الحصص المعتمدة حسب المسلك</h3>', track_chart,
        '</section>',
        '<section class="visual-section">',
        '<h2>جدول الأنشطة المنجزة والمصادق عليها</h2>',
        '<table><thead><tr><th>التاريخ</th><th>المسلك</th>'
        '<th>المادة</th><th>النشاط المنجز</th><th>مرجع الحصة</th></tr></thead>',
        '<tbody>', activity_rows, '</tbody></table></section>',
        '<section class="visual-section">',
        '<h2>الصور التوثيقية المختارة</h2>',
        '<div class="gallery">', gallery, '</div>',
        '</section>',
    ])
