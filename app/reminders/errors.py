class ReminderError(Exception):
    """Base class for expected reminder read failures."""


class ReminderNotFoundError(ReminderError):
    pass


class ReminderAccessForbiddenError(ReminderError):
    pass


class ReminderQueryValidationError(ReminderError):
    pass


class ReminderActionConflictError(ReminderError):
    pass
