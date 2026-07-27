class AlertError(Exception):
    """Base class for expected caregiver alert API failures."""


class AlertNotFoundError(AlertError):
    pass


class AlertTransitionConflictError(AlertError):
    pass
