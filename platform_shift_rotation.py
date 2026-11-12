"""Independent alternating AM/PM weeks for each center, with no schema changes.

The two anchor settings are integer values in the already deployed v2_settings.
Slot.kind uses existing 25-char text: professional_am/pm, pedagogical_am/pm.
Legacy professional/pedagogical slots stay 'both' until manually reclassified.
"""
from datetime import date, timedelta
from sqlalchemy import select

from platform_v2_models import Setting

SHIFT_OPTIONS = (("morning", "أسبوع صباحي"), ("evening", "أسبوع مسائي"),
                 ("both", "كل الأسابيع (حصة قديمة / ثابتة)"))
SHIFT_LABELS = {code: label for code, label in SHIFT_OPTIONS}
SUFFIX = {"morning": "_am", "evening": "_pm", "both": ""}
PREFIXES = ("professional", "pedagogical")


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def slot_shift(slot) -> str:
    kind = slot.kind or ""
    if kind.endswith("_am"):
        return "morning"
    if kind.endswith("_pm"):
        return "evening"
    return "both"


def encode_slot_kind(subject: str, shift: str) -> str:
    if shift not in SUFFIX:
        raise ValueError("invalid weekly shift")
    prefix = "professional" if subject == "مهني" else "pedagogical"
    return prefix + SUFFIX[shift]


def center_rotation(db, center_id: int):
    keys = (f"shift_anchor_{center_id}", f"shift_phase_{center_id}")
    rows = db.scalars(select(Setting).where(Setting.key.in_(keys))).all()
    config = {row.key: row.value for row in rows}
    if any(key not in config for key in keys):
        return None
    ordinal, phase = config[keys[0]], config[keys[1]]
    if phase not in (0, 1):
        return None
    try:
        anchor = date.fromordinal(ordinal)
    except (ValueError, OverflowError):
        return None
    return anchor, ("morning" if phase == 0 else "evening")


def week_shift(db, center_id: int, on_date: date) -> str | None:
    config = center_rotation(db, center_id)
    if config is None:
        return None
    anchor, anchor_shift = config
    distance = (monday(on_date) - monday(anchor)).days // 7
    return anchor_shift if distance % 2 == 0 else (
        "evening" if anchor_shift == "morning" else "morning"
    )


def slot_runs_on(db, slot, on_date: date) -> bool:
    if slot.specific_date is not None:
        return slot.specific_date == on_date
    required = slot_shift(slot)
    active = week_shift(db, slot.center_id, on_date)
    return required == "both" or active is None or required == active


def shift_overlap(first, second) -> bool:
    """Different weeks do not conflict; an exceptional date still may."""
    a, b = slot_shift(first), slot_shift(second)
    return a == "both" or b == "both" or a == b


def weekday_label(db, center_id, day):
    shift = week_shift(db, center_id, day)
    return SHIFT_LABELS.get(shift, "التناوب غير مضبوط")
