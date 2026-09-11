from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.time import utc_now
from app.event_evaluations.model import EvaluationSeverity
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.household_access.errors import AccessForbiddenError
from app.household_access.model import CaregiverPatientAssignment
from app.household_access.model import PatientAccessCode
from app.households.model import Household, HouseholdStatus
from app.patients.errors import PatientNotFoundError
from app.patients.model import ElderlyPatient
from app.patients.schema import (
    CurrentHealthSummary,
    LatestReading,
    MonitoringStatus,
    PatientCreate,
    PatientCreated,
    PatientAccessStatus,
    PatientAccessSummary,
    MonitoringSettingsResponse,
    MonitoringSettingsUpdate,
    ThresholdMode,
)
from app.users.model import AccountStatus, User, UserRole


ACCEPTED_EVENT_STATUSES = (
    ValidationStatus.VALID_REALTIME,
    ValidationStatus.DELAYED_USABLE,
)
ACTIVE_ALERT_STATUSES = (AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED)
SUMMARY_METRICS = (MetricType.HEART_RATE, MetricType.SPO2)


@dataclass(frozen=True)
class PatientReadRow:
    patient: ElderlyPatient
    user: User
    assignment: CaregiverPatientAssignment | None
    current_summary: CurrentHealthSummary
    patient_access: PatientAccessSummary | None = None


def _patient_scope(actor: User) -> Select:
    base = (
        select(ElderlyPatient.patient_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .where(
            ElderlyPatient.archived_at.is_(None),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
    )
    if actor.role is UserRole.CAREGIVER:
        return base.where(
            ElderlyPatient.patient_id.in_(
                select(CaregiverPatientAssignment.patient_id).where(
                    CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
                    CaregiverPatientAssignment.unassigned_at.is_(None),
                )
            )
        )
    if actor.role is UserRole.CARE_ADMIN:
        return base.where(Household.created_by_user_id == actor.user_id)
    return base.where(False)


def _summary_map(
    db: Session, patients: list[ElderlyPatient]
) -> dict[UUID, CurrentHealthSummary]:
    if not patients:
        return {}
    patient_ids = [patient.patient_id for patient in patients]
    ranked_events = (
        select(
            HealthEvent.patient_id.label("patient_id"),
            HealthEvent.metric_type.label("metric_type"),
            HealthEvent.numeric_value.label("numeric_value"),
            HealthEvent.metric_unit.label("metric_unit"),
            HealthEvent.recorded_at.label("recorded_at"),
            func.row_number().over(
                partition_by=(HealthEvent.patient_id, HealthEvent.metric_type),
                order_by=(
                    HealthEvent.recorded_at.desc(),
                    HealthEvent.received_at.desc(),
                    HealthEvent.event_id.desc(),
                ),
            ).label("position"),
        )
        .where(
            HealthEvent.patient_id.in_(patient_ids),
            HealthEvent.metric_type.in_(SUMMARY_METRICS),
            HealthEvent.validation_status.in_(ACCEPTED_EVENT_STATUSES),
            HealthEvent.numeric_value.is_not(None),
        )
        .subquery()
    )
    event_rows = db.execute(
        select(ranked_events).where(ranked_events.c.position == 1)
    ).mappings().all()
    readings: dict[UUID, dict[MetricType, LatestReading]] = {}
    for row in event_rows:
        readings.setdefault(row["patient_id"], {})[row["metric_type"]] = LatestReading(
            value=row["numeric_value"],
            unit=row["metric_unit"],
            recorded_at=row["recorded_at"],
        )

    severity_rank = case(
        (Alert.severity == EvaluationSeverity.CRITICAL, 3),
        (Alert.severity == EvaluationSeverity.WARNING, 2),
        else_=1,
    )
    alert_rows = db.execute(
        select(
            Alert.patient_id,
            func.count(Alert.alert_id),
            func.max(severity_rank),
        )
        .where(
            Alert.patient_id.in_(patient_ids),
            Alert.status.in_(ACTIVE_ALERT_STATUSES),
        )
        .group_by(Alert.patient_id)
    ).all()
    alerts = {
        patient_id: (count, rank) for patient_id, count, rank in alert_rows
    }
    result = {}
    for patient in patients:
        patient_readings = readings.get(patient.patient_id, {})
        heart_rate = patient_readings.get(MetricType.HEART_RATE)
        spo2 = patient_readings.get(MetricType.SPO2)
        check_ins = [
            reading.recorded_at
            for reading in (heart_rate, spo2)
            if reading is not None
        ]
        alert_count, highest_rank = alerts.get(patient.patient_id, (0, None))
        highest = {
            3: EvaluationSeverity.CRITICAL,
            2: EvaluationSeverity.WARNING,
            1: EvaluationSeverity.INFO,
        }.get(highest_rank)
        if highest is EvaluationSeverity.CRITICAL:
            monitoring = MonitoringStatus.CRITICAL
        elif highest is EvaluationSeverity.WARNING:
            monitoring = MonitoringStatus.WARNING
        elif check_ins:
            monitoring = MonitoringStatus.STABLE
        else:
            monitoring = MonitoringStatus.NO_DATA
        result[patient.patient_id] = CurrentHealthSummary(
            latest_heart_rate=heart_rate,
            latest_spo2=spo2,
            last_check_in=max(check_ins) if check_ins else None,
            active_alert_count=alert_count,
            highest_active_alert_severity=highest,
            monitoring_status=monitoring,
            device_connection_status=patient.integration_status,
            last_device_sync_at=patient.last_sync_at,
        )
    return result


def _assignments_for_actor(
    db: Session, actor: User, patient_ids: list[UUID]
) -> dict[UUID, CaregiverPatientAssignment]:
    if actor.role is not UserRole.CAREGIVER or not patient_ids:
        return {}
    return {
        assignment.patient_id: assignment
        for assignment in db.scalars(
            select(CaregiverPatientAssignment).where(
                CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
                CaregiverPatientAssignment.patient_id.in_(patient_ids),
                CaregiverPatientAssignment.unassigned_at.is_(None),
            )
        ).all()
    }


def list_patients(
    db: Session,
    actor: User,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[PatientReadRow], int]:
    filters: list[Any] = [ElderlyPatient.patient_id.in_(_patient_scope(actor))]
    if search:
        filters.append(User.full_name.ilike(f"%{search}%"))
    total = db.scalar(
        select(func.count(ElderlyPatient.patient_id))
        .join(User, User.user_id == ElderlyPatient.user_id)
        .where(*filters)
    ) or 0
    rows = db.execute(
        select(ElderlyPatient, User)
        .join(User, User.user_id == ElderlyPatient.user_id)
        .where(*filters)
        .order_by(User.full_name, ElderlyPatient.patient_id)
        .limit(limit)
        .offset(offset)
    ).all()
    patients = [patient for patient, _user in rows]
    summaries = _summary_map(db, patients)
    assignments = _assignments_for_actor(
        db, actor, [patient.patient_id for patient in patients]
    )
    return [
        PatientReadRow(
            patient,
            user,
            assignments.get(patient.patient_id),
            summaries[patient.patient_id],
        )
        for patient, user in rows
    ], total


def get_patient(db: Session, actor: User, patient_id: UUID) -> PatientReadRow:
    row = db.execute(
        select(ElderlyPatient, User)
        .join(User, User.user_id == ElderlyPatient.user_id)
        .where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.patient_id.in_(_patient_scope(actor)),
        )
    ).one_or_none()
    if row is None:
        raise PatientNotFoundError("Patient not found.")
    patient, user = row
    assignment = _assignments_for_actor(db, actor, [patient_id]).get(patient_id)
    return PatientReadRow(
        patient,
        user,
        assignment,
        _summary_map(db, [patient])[patient_id],
        _patient_access_summary(db, patient_id),
    )


def _patient_access_summary(db: Session, patient_id: UUID) -> PatientAccessSummary:
    """Return only the code metadata safe and necessary for Patient Detail."""
    now = utc_now()
    newest_usable = (
        select(
            PatientAccessCode.access_code_id.label("access_code_id"),
            PatientAccessCode.expires_at.label("expires_at"),
            func.row_number()
            .over(
                order_by=(
                    PatientAccessCode.created_at.desc(),
                    PatientAccessCode.access_code_id.desc(),
                )
            )
            .label("position"),
        )
        .where(
            PatientAccessCode.patient_id == patient_id,
            PatientAccessCode.used_at.is_(None),
            PatientAccessCode.revoked_at.is_(None),
            PatientAccessCode.expires_at > now,
        )
        .subquery()
    )
    access = db.execute(
        select(
            select(func.max(PatientAccessCode.used_at))
            .where(PatientAccessCode.patient_id == patient_id)
            .scalar_subquery()
            .label("connected_at"),
            select(newest_usable.c.access_code_id)
            .where(newest_usable.c.position == 1)
            .scalar_subquery()
            .label("pending_access_code_id"),
            select(newest_usable.c.expires_at)
            .where(newest_usable.c.position == 1)
            .scalar_subquery()
            .label("pending_expires_at"),
        )
    ).one()
    if access.connected_at is not None:
        return PatientAccessSummary(
            status=PatientAccessStatus.CONNECTED,
            pending_access_code_id=None,
            pending_expires_at=None,
            connected_at=access.connected_at,
        )
    if access.pending_access_code_id is not None:
        return PatientAccessSummary(
            status=PatientAccessStatus.INVITE_PENDING,
            pending_access_code_id=access.pending_access_code_id,
            pending_expires_at=access.pending_expires_at,
            connected_at=None,
        )
    return PatientAccessSummary(
        status=PatientAccessStatus.NOT_CONNECTED,
        pending_access_code_id=None,
        pending_expires_at=None,
        connected_at=None,
    )


def patient_read_payload(row: PatientReadRow, *, detail: bool) -> dict:
    patient, user = row.patient, row.user
    payload = {
        "patient_id": patient.patient_id,
        "user_id": patient.user_id,
        "household_id": patient.household_id,
        "full_name": user.full_name,
        "birthdate": patient.birthdate,
        "sex": patient.sex,
        "phone_number": user.phone_number,
        "address_or_room": patient.address_or_room,
        "account_status": user.account_status,
        "created_at": patient.created_at,
        "current_summary": row.current_summary,
    }
    if detail:
        payload.update(
            patient_access=row.patient_access,
            emergency_contact_name=patient.emergency_contact_name,
            emergency_contact_phone=patient.emergency_contact_phone,
            known_conditions=patient.known_conditions,
            medications=patient.medications,
            baseline_heart_rate=patient.baseline_heart_rate,
            baseline_spo2=patient.baseline_spo2,
            monitoring_notes=patient.health_notes,
            archived_at=patient.archived_at,
            assignment=row.assignment,
            normal_hr_min=patient.normal_hr_min,
            normal_hr_max=patient.normal_hr_max,
            usual_spo2_min=patient.usual_spo2_min,
            usual_spo2_max=patient.usual_spo2_max,
            threshold_mode=_threshold_mode(patient),
        )
    return payload


class MonitoringSettingsValidationError(ValueError):
    pass


def _threshold_mode(patient: ElderlyPatient) -> ThresholdMode:
    if (
        patient.normal_hr_min == 60
        and patient.normal_hr_max == 100
        and patient.usual_spo2_min == 95
        and patient.usual_spo2_max is None
    ):
        return ThresholdMode.DEFAULT
    return ThresholdMode.CUSTOM


def update_monitoring_settings(
    db: Session,
    actor: User,
    patient_id: UUID,
    payload: MonitoringSettingsUpdate,
) -> MonitoringSettingsResponse:
    row = db.execute(
        select(ElderlyPatient)
        .where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.patient_id.in_(_patient_scope(actor)),
        )
    ).scalar_one_or_none()
    if row is None:
        raise PatientNotFoundError("Patient not found.")

    values = {
        "normal_hr_min": row.normal_hr_min,
        "normal_hr_max": row.normal_hr_max,
        "usual_spo2_min": row.usual_spo2_min,
        "usual_spo2_max": row.usual_spo2_max,
    }
    values.update(payload.model_dump(exclude_unset=True))
    if values["normal_hr_min"] > values["normal_hr_max"]:
        raise MonitoringSettingsValidationError(
            "normal_hr_min must be less than or equal to normal_hr_max."
        )
    if (
        values["usual_spo2_max"] is not None
        and values["usual_spo2_min"] > values["usual_spo2_max"]
    ):
        raise MonitoringSettingsValidationError(
            "usual_spo2_min must be less than or equal to usual_spo2_max."
        )

    for field, value in values.items():
        setattr(row, field, value)
    db.flush()
    return MonitoringSettingsResponse(
        patient_id=row.patient_id,
        threshold_mode=_threshold_mode(row),
        normal_hr_min=row.normal_hr_min,
        normal_hr_max=row.normal_hr_max,
        usual_spo2_min=row.usual_spo2_min,
        usual_spo2_max=row.usual_spo2_max,
        updated_at=row.updated_at,
    )


def create_patient(db: Session, actor: User, household_id: UUID,
                   payload: PatientCreate) -> PatientCreated:
    household = db.get(Household, household_id)
    permitted = False
    if (actor.account_status is AccountStatus.ACTIVE and household is not None
            and household.household_status is HouseholdStatus.ACTIVE
            and household.archived_at is None):
        if actor.role is UserRole.CARE_ADMIN:
            permitted = household.created_by_user_id == actor.user_id
        elif actor.role is UserRole.CAREGIVER:
            permitted = db.scalar(
                select(CaregiverPatientAssignment.assignment_id)
                .join(ElderlyPatient, ElderlyPatient.patient_id == CaregiverPatientAssignment.patient_id)
                .where(
                    CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
                    CaregiverPatientAssignment.unassigned_at.is_(None),
                    ElderlyPatient.household_id == household_id,
                    ElderlyPatient.archived_at.is_(None),
                ).limit(1)
            ) is not None
    if not permitted:
        raise AccessForbiddenError("Actor is not permitted to create patients in this household.")

    user = User(full_name=payload.full_name, phone_number=payload.phone_number,
                role=UserRole.ELDERLY_PATIENT)
    db.add(user)
    db.flush()
    fields = payload.model_dump(exclude={"full_name", "phone_number", "monitoring_notes"})
    patient = ElderlyPatient(
        user_id=user.user_id, household_id=household_id,
        health_notes=payload.monitoring_notes, **fields,
    )
    db.add(patient)
    db.flush()
    assignment = None
    if actor.role is UserRole.CAREGIVER:
        assignment = CaregiverPatientAssignment(
            caregiver_user_id=actor.user_id, patient_id=patient.patient_id,
            assigned_by_user_id=actor.user_id,
        )
        db.add(assignment)
        db.flush()
    return PatientCreated(
        **payload.model_dump(), patient_id=patient.patient_id, user_id=user.user_id,
        household_id=household_id, account_status=user.account_status,
        archived_at=patient.archived_at, assignment=assignment, created_at=patient.created_at,
    )
