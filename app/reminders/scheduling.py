from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.reminders.enums import (
    ReminderOccurrenceStatus,
    ReminderTemplateStatus,
)
from app.reminders.model import ReminderOccurrence, ReminderTemplate


MATERIALIZATION_WINDOW_DAYS = 60
MAX_RECURRENCE_INTERVAL = 365
WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAY_INDEX = {code: index for index, code in enumerate(WEEKDAY_CODES)}
SUPPORTED_RULE_KEYS = {"FREQ", "INTERVAL", "BYDAY"}


class ReminderScheduleValidationError(ValueError):
    """Raised when a reminder recurrence or local schedule is invalid."""


@dataclass(frozen=True)
class ReminderScheduleRule:
    frequency: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()

    def canonical(self) -> str:
        parts = [f"FREQ={self.frequency}"]
        if self.interval != 1:
            parts.append(f"INTERVAL={self.interval}")
        if self.weekdays:
            parts.append(
                "BYDAY=" + ",".join(WEEKDAY_CODES[index] for index in self.weekdays)
            )
        return ";".join(parts)


def parse_schedule_rule(value: str | None) -> ReminderScheduleRule | None:
    """Parse Alera's deliberately limited RFC 5545 RRULE subset."""
    if value is None:
        return None
    value = value.strip().upper()
    if not value:
        raise ReminderScheduleValidationError("schedule_rule must not be blank.")
    if value.startswith("RRULE:"):
        value = value.removeprefix("RRULE:")

    fields: dict[str, str] = {}
    for component in value.split(";"):
        if not component or "=" not in component:
            raise ReminderScheduleValidationError("schedule_rule is malformed.")
        key, raw = component.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if not key or not raw:
            raise ReminderScheduleValidationError("schedule_rule is malformed.")
        if key in fields:
            raise ReminderScheduleValidationError(
                f"schedule_rule contains duplicate {key}."
            )
        if key not in SUPPORTED_RULE_KEYS:
            raise ReminderScheduleValidationError(
                f"schedule_rule key {key} is not supported."
            )
        fields[key] = raw

    frequency = fields.get("FREQ")
    if frequency not in {"DAILY", "WEEKLY"}:
        raise ReminderScheduleValidationError(
            "schedule_rule FREQ must be DAILY or WEEKLY."
        )

    raw_interval = fields.get("INTERVAL", "1")
    try:
        interval = int(raw_interval)
    except ValueError as exc:
        raise ReminderScheduleValidationError(
            "schedule_rule INTERVAL must be an integer."
        ) from exc
    if not 1 <= interval <= MAX_RECURRENCE_INTERVAL:
        raise ReminderScheduleValidationError(
            f"schedule_rule INTERVAL must be between 1 and {MAX_RECURRENCE_INTERVAL}."
        )

    raw_days = fields.get("BYDAY")
    weekdays: tuple[int, ...] = ()
    if raw_days is not None:
        if frequency != "WEEKLY":
            raise ReminderScheduleValidationError(
                "schedule_rule BYDAY is supported only for WEEKLY reminders."
            )
        day_codes = [part.strip() for part in raw_days.split(",")]
        if not day_codes or any(code not in WEEKDAY_INDEX for code in day_codes):
            raise ReminderScheduleValidationError(
                "schedule_rule BYDAY must contain MO,TU,WE,TH,FR,SA,SU values."
            )
        if len(set(day_codes)) != len(day_codes):
            raise ReminderScheduleValidationError(
                "schedule_rule BYDAY must not contain duplicates."
            )
        weekdays = tuple(sorted(WEEKDAY_INDEX[code] for code in day_codes))

    return ReminderScheduleRule(
        frequency=frequency,
        interval=interval,
        weekdays=weekdays,
    )


def normalize_schedule_rule(value: str | None) -> str | None:
    parsed = parse_schedule_rule(value)
    return parsed.canonical() if parsed is not None else None


def _require_aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderScheduleValidationError(
            f"{field} must include timezone information."
        )
    return value.astimezone(timezone.utc)


def _strict_local_to_utc(local_value: datetime, zone: ZoneInfo) -> datetime:
    """Resolve a wall time, rejecting DST gaps and choosing the earlier fold."""
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = local_value.replace(tzinfo=zone, fold=fold)
        utc_value = aware.astimezone(timezone.utc)
        round_trip = utc_value.astimezone(zone)
        if round_trip.replace(tzinfo=None) == local_value:
            candidates.append(utc_value)
    unique = sorted(set(candidates))
    if not unique:
        raise ReminderScheduleValidationError(
            f"Local reminder time {local_value.isoformat()} does not exist in {zone.key}."
        )
    return unique[0]


def _occurs_on_date(
    candidate: date,
    *,
    start_date: date,
    rule: ReminderScheduleRule | None,
) -> bool:
    if candidate < start_date:
        return False
    if rule is None:
        return candidate == start_date
    elapsed_days = (candidate - start_date).days
    if rule.frequency == "DAILY":
        return elapsed_days % rule.interval == 0
    start_week = start_date - timedelta(days=start_date.weekday())
    candidate_week = candidate - timedelta(days=candidate.weekday())
    elapsed_weeks = (candidate_week - start_week).days // 7
    weekdays = rule.weekdays or (start_date.weekday(),)
    return elapsed_weeks % rule.interval == 0 and candidate.weekday() in weekdays


def occurrence_times_for_window(
    template: ReminderTemplate,
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[datetime]:
    """Return scheduled UTC instants in the half-open [start, end) window."""
    start_utc = _require_aware_utc(window_start, field="window_start")
    end_utc = _require_aware_utc(window_end, field="window_end")
    if end_utc <= start_utc:
        raise ReminderScheduleValidationError(
            "window_end must be greater than window_start."
        )
    try:
        zone = ZoneInfo(template.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ReminderScheduleValidationError(
            "Reminder template timezone is invalid."
        ) from exc

    rule = parse_schedule_rule(template.schedule_rule)
    local_start_date = max(
        template.start_date,
        (start_utc.astimezone(zone) - timedelta(days=1)).date(),
    )
    local_end_date = (end_utc.astimezone(zone) + timedelta(days=1)).date()
    results: list[datetime] = []
    candidate = local_start_date
    while candidate <= local_end_date:
        if _occurs_on_date(candidate, start_date=template.start_date, rule=rule):
            local_value = datetime.combine(candidate, template.start_time)
            scheduled_at = _strict_local_to_utc(local_value, zone)
            if start_utc <= scheduled_at < end_utc:
                results.append(scheduled_at)
        candidate += timedelta(days=1)
    return results


def materialize_reminder_occurrences(
    db: Session,
    *,
    template: ReminderTemplate,
    window_start: datetime,
    window_days: int = MATERIALIZATION_WINDOW_DAYS,
) -> int:
    """Insert missing occurrences for one active template without committing."""
    if template.status is not ReminderTemplateStatus.ACTIVE:
        return 0
    if not 1 <= window_days <= MATERIALIZATION_WINDOW_DAYS:
        raise ReminderScheduleValidationError(
            f"window_days must be between 1 and {MATERIALIZATION_WINDOW_DAYS}."
        )
    start_utc = _require_aware_utc(window_start, field="window_start")
    scheduled_values = occurrence_times_for_window(
        template,
        window_start=start_utc,
        window_end=start_utc + timedelta(days=window_days),
    )
    if not scheduled_values:
        return 0

    rows = [
        {
            "reminder_template_id": template.reminder_template_id,
            "scheduled_at": scheduled_at,
            "due_at": scheduled_at
            + timedelta(minutes=template.due_after_minutes),
            "status": ReminderOccurrenceStatus.UPCOMING,
        }
        for scheduled_at in scheduled_values
    ]
    statement = (
        insert(ReminderOccurrence)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[
                ReminderOccurrence.reminder_template_id,
                ReminderOccurrence.scheduled_at,
            ]
        )
        .returning(ReminderOccurrence.reminder_occurrence_id)
    )
    return len(list(db.scalars(statement)))


def materialize_active_reminder_occurrences(
    db: Session,
    *,
    window_start: datetime,
    window_days: int = MATERIALIZATION_WINDOW_DAYS,
) -> int:
    """Insert missing occurrences for every active template without committing."""
    templates = list(
        db.scalars(
            select(ReminderTemplate)
            .where(ReminderTemplate.status == ReminderTemplateStatus.ACTIVE)
            .order_by(ReminderTemplate.reminder_template_id)
        )
    )
    return sum(
        materialize_reminder_occurrences(
            db,
            template=template,
            window_start=window_start,
            window_days=window_days,
        )
        for template in templates
    )
