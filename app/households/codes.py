import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session


HOUSEHOLD_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_household_code() -> str:
    """Generate a readable household identifier, excluding ambiguous characters."""
    raw = "".join(secrets.choice(HOUSEHOLD_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_household_code(value: str) -> str | None:
    """Return an eight-character household code in its canonical form."""
    if not isinstance(value, str):
        return None
    compact = value.strip().upper().replace("-", "")
    if len(compact) != 8 or any(
        character not in HOUSEHOLD_CODE_ALPHABET for character in compact
    ):
        return None
    return f"{compact[:4]}-{compact[4:]}"


def allocate_household_code(db: Session, *, max_attempts: int = 20) -> str:
    """Allocate an unused code; the database unique constraint closes race windows."""
    from app.households.model import Household

    for _ in range(max_attempts):
        candidate = generate_household_code()
        if db.scalar(
            select(Household.household_id).where(
                Household.household_code == candidate
            )
        ) is None:
            return candidate
    raise RuntimeError("Could not allocate a unique household code.")
