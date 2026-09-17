class ActivityError(Exception):
    """Base class for expected activity API failures."""


class ActivityAccessError(ActivityError):
    pass


class ActivityConflictError(ActivityError):
    pass