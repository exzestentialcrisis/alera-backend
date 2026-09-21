import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    SmallInteger,
    Numeric,
    String,
    Text,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class Sex(str, enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class HealthPlatform(str, enum.Enum):
    HEALTH_CONNECT = "HEALTH_CONNECT"
    SIMULATOR = "SIMULATOR"
    MANUAL_TEST = "MANUAL_TEST"


class IntegrationStatus(str, enum.Enum):
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED = "CONNECTED"
    SYNCING = "SYNCING"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


class ElderlyPatient(Base):
    __tablename__ = "elderly_patients"
    __table_args__ = (
        CheckConstraint("normal_hr_min > 0", name="ck_patient_hr_min_positive"),
        CheckConstraint("normal_hr_max > 0", name="ck_patient_hr_max_positive"),
        CheckConstraint(
            "normal_hr_min <= normal_hr_max",
            name="ck_patient_hr_range_order",
        ),
        CheckConstraint(
            "usual_spo2_min BETWEEN 0 AND 100",
            name="ck_patient_spo2_min_range",
        ),
        CheckConstraint(
            "usual_spo2_max IS NULL OR usual_spo2_max BETWEEN 0 AND 100",
            name="ck_patient_spo2_max_range",
        ),
        CheckConstraint(
            "usual_spo2_max IS NULL OR usual_spo2_min <= usual_spo2_max",
            name="ck_patient_spo2_range_order",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        unique=True,
        nullable=False,
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("households.household_id"),
        nullable=False,
    )

    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[Sex | None] = mapped_column(
        Enum(Sex, name="patient_sex"), nullable=True
    )

    address_or_room: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_photo_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    emergency_contact_phone: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    baseline_heart_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    baseline_spo2: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    known_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    medications: Mapped[str | None] = mapped_column(Text, nullable=True)
    doctor_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    normal_hr_min: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=60,
        server_default="60",
    )

    normal_hr_max: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=100,
        server_default="100",
    )

    usual_spo2_min: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=95,
        server_default="95",
    )

    usual_spo2_max: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    health_platform: Mapped[HealthPlatform] = mapped_column(
        Enum(HealthPlatform, name="health_platform"),
        nullable=False,
        default=HealthPlatform.SIMULATOR,
    )

    device_brand: Mapped[str | None] = mapped_column(String(50), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    integration_status: Mapped[IntegrationStatus] = mapped_column(
        Enum(IntegrationStatus, name="integration_status"),
        nullable=False,
        default=IntegrationStatus.NOT_CONNECTED,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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


def validate_patient_thresholds(patient: ElderlyPatient) -> None:
    """Validate monitoring ranges before an insert or update is flushed."""
    hr_min = patient.normal_hr_min if patient.normal_hr_min is not None else 60
    hr_max = patient.normal_hr_max if patient.normal_hr_max is not None else 100
    spo2_min = patient.usual_spo2_min if patient.usual_spo2_min is not None else 95
    spo2_max = patient.usual_spo2_max

    if hr_min <= 0 or hr_max <= 0:
        raise ValueError("Heart-rate thresholds must be greater than 0.")
    if hr_min > hr_max:
        raise ValueError("normal_hr_min must be less than or equal to normal_hr_max.")
    if not 0 <= spo2_min <= 100:
        raise ValueError("usual_spo2_min must be between 0 and 100.")
    if spo2_max is not None:
        if not 0 <= spo2_max <= 100:
            raise ValueError("usual_spo2_max must be between 0 and 100.")
        if spo2_min > spo2_max:
            raise ValueError(
                "usual_spo2_min must be less than or equal to usual_spo2_max."
            )


@event.listens_for(ElderlyPatient, "before_insert")
@event.listens_for(ElderlyPatient, "before_update")
def _validate_patient_thresholds_before_flush(
    _mapper,
    _connection,
    patient: ElderlyPatient,
) -> None:
    validate_patient_thresholds(patient)
