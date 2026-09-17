import enum


class ReminderCategory(str, enum.Enum):
    MEDICATION = "MEDICATION"
    HEALTH_CHECK = "HEALTH_CHECK"
    HYDRATION = "HYDRATION"
    MEAL = "MEAL"
    MOBILITY = "MOBILITY"
    APPOINTMENT = "APPOINTMENT"
    CHECK_IN = "CHECK_IN"
    DEVICE_TASK = "DEVICE_TASK"
    OTHER = "OTHER"


class ReminderPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class ReminderTemplateStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class ReminderOccurrenceStatus(str, enum.Enum):
    UPCOMING = "UPCOMING"
    DUE = "DUE"
    SNOOZED = "SNOOZED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    CANCELED = "CANCELED"
    COMPLETED_LATE = "COMPLETED_LATE"


class ReminderActionType(str, enum.Enum):
    MARK_DUE = "MARK_DUE"
    MARK_COMPLETED = "MARK_COMPLETED"
    SNOOZE = "SNOOZE"
    REQUEST_HELP = "REQUEST_HELP"
    CAREGIVER_OVERRIDE = "CAREGIVER_OVERRIDE"
    MARK_MISSED = "MARK_MISSED"
    MARK_MISSED_HANDLED = "MARK_MISSED_HANDLED"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    ADD_NOTE = "ADD_NOTE"
    FOLLOW_UP = "FOLLOW_UP"


class ReminderNotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    PUSH = "PUSH"
    SMS = "SMS"
