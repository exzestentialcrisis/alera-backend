import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.households.codes import generate_household_code


class HouseholdStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class Household(Base):
    __tablename__ = "households"
    __table_args__ = (
        UniqueConstraint("household_code", name="uq_households_household_code"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )

    household_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    household_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=generate_household_code,
    )

    address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    barangay: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    household_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    household_status: Mapped[HouseholdStatus] = mapped_column(
        Enum(HouseholdStatus, name="household_status"),
        nullable=False,
        default=HouseholdStatus.ACTIVE,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


@event.listens_for(Household, "before_update")
def _prevent_household_code_update(_mapper, _connection, household: Household) -> None:
    if inspect(household).attrs.household_code.history.has_changes():
        raise ValueError("household_code is immutable.")
