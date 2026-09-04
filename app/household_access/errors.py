class HouseholdAccessError(Exception):
    pass


class AccessForbiddenError(HouseholdAccessError):
    pass


class AccessNotFoundError(HouseholdAccessError):
    pass


class AccessConflictError(HouseholdAccessError):
    pass
