from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.alerts.access import accessible_patient_ids
from app.core.time import utc_now
from app.help_requests.model import HelpRequest, HelpRequestStatus
from app.patients.model import ElderlyPatient
from app.users.model import User


class HelpRequestConflictError(Exception):
    pass


class HelpRequestNotFoundError(Exception):
    pass


class HelpRequestTransitionConflictError(Exception):
    pass


def help_request_payload(
    request: HelpRequest,
    *,
    patient: ElderlyPatient | None = None,
    user: User | None = None,
    idempotent: bool = False,
) -> dict:
    return {
        "help_request_id": request.help_request_id,
        "patient_id": request.patient_id,
        "status": request.status,
        "message": request.message,
        "client_action_id": request.client_action_id,
        "requested_at": request.requested_at,
        "acknowledged_by_user_id": request.acknowledged_by_user_id,
        "acknowledged_at": request.acknowledged_at,
        "resolved_by_user_id": request.resolved_by_user_id,
        "resolved_at": request.resolved_at,
        "updated_at": request.updated_at,
        "patient_display_name": (
            (patient.nickname or user.full_name)
            if patient is not None and user is not None
            else (patient.nickname if patient is not None else None)
        ),
        "idempotent": idempotent,
    }


def active_help_request(
    db: Session,
    *,
    patient_id: UUID,
) -> HelpRequest | None:
    return db.scalar(
        select(HelpRequest)
        .where(
            HelpRequest.patient_id == patient_id,
            HelpRequest.status.in_(
                [
                    HelpRequestStatus.PENDING,
                    HelpRequestStatus.ACKNOWLEDGED,
                ]
            ),
        )
        .order_by(
            HelpRequest.requested_at.desc(),
            HelpRequest.help_request_id.desc(),
        )
        .limit(1)
    )


def _matching_replay(
    request: HelpRequest,
    *,
    patient_id: UUID,
    message: str | None,
) -> bool:
    return request.patient_id == patient_id and request.message == message


def create_help_request(
    db: Session,
    *,
    patient: ElderlyPatient,
    client_action_id: UUID,
    message: str | None,
) -> tuple[HelpRequest, bool]:
    existing = db.scalar(
        select(HelpRequest).where(
            HelpRequest.client_action_id == client_action_id
        )
    )
    if existing is not None:
        if not _matching_replay(
            existing,
            patient_id=patient.patient_id,
            message=message,
        ):
            raise HelpRequestConflictError(
                "client_action_id was already used for another request."
            )
        return existing, True

    if active_help_request(db, patient_id=patient.patient_id) is not None:
        raise HelpRequestConflictError(
            "An unresolved help request already exists."
        )

    request = HelpRequest(
        patient_id=patient.patient_id,
        client_action_id=client_action_id,
        message=message,
    )
    savepoint = db.begin_nested()
    try:
        db.add(request)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()

        existing = db.scalar(
            select(HelpRequest).where(
                HelpRequest.client_action_id == client_action_id
            )
        )
        if existing is not None:
            if not _matching_replay(
                existing,
                patient_id=patient.patient_id,
                message=message,
            ):
                raise HelpRequestConflictError(
                    "client_action_id was already used for another request."
                )
            return existing, True

        if active_help_request(db, patient_id=patient.patient_id) is not None:
            raise HelpRequestConflictError(
                "An unresolved help request already exists."
            )
        raise

    return request, False



def _patient_context(
    db: Session,
    request: HelpRequest,
) -> tuple[ElderlyPatient | None, User | None]:
    patient = db.get(ElderlyPatient, request.patient_id)
    user = db.get(User, patient.user_id) if patient is not None else None
    return patient, user


def list_help_requests(
    db: Session,
    *,
    actor: User,
    statuses: list[HelpRequestStatus] | None,
    patient_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[
    list[tuple[HelpRequest, ElderlyPatient | None, User | None]],
    int,
]:
    effective_statuses = statuses or [
        HelpRequestStatus.PENDING,
        HelpRequestStatus.ACKNOWLEDGED,
    ]

    filters = [
        HelpRequest.patient_id.in_(accessible_patient_ids(actor)),
        HelpRequest.status.in_(effective_statuses),
    ]
    if patient_id is not None:
        filters.append(HelpRequest.patient_id == patient_id)

    total = db.scalar(
        select(func.count(HelpRequest.help_request_id)).where(*filters)
    ) or 0

    status_rank = case(
        (HelpRequest.status == HelpRequestStatus.PENDING, 0),
        (HelpRequest.status == HelpRequestStatus.ACKNOWLEDGED, 1),
        else_=2,
    )

    requests = list(
        db.scalars(
            select(HelpRequest)
            .where(*filters)
            .order_by(
                status_rank,
                HelpRequest.requested_at.desc(),
                HelpRequest.help_request_id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
    )

    if not requests:
        return [], total

    patient_ids = {item.patient_id for item in requests}
    patients = {
        item.patient_id: item
        for item in db.scalars(
            select(ElderlyPatient).where(
                ElderlyPatient.patient_id.in_(patient_ids)
            )
        ).all()
    }

    user_ids = {item.user_id for item in patients.values()}
    users = {
        item.user_id: item
        for item in db.scalars(
            select(User).where(User.user_id.in_(user_ids))
        ).all()
    }

    return [
        (
            item,
            patients.get(item.patient_id),
            users.get(patients[item.patient_id].user_id)
            if item.patient_id in patients
            else None,
        )
        for item in requests
    ], total


def get_help_request(
    db: Session,
    *,
    help_request_id: UUID,
    actor: User,
) -> tuple[HelpRequest, ElderlyPatient | None, User | None]:
    request = db.scalar(
        select(HelpRequest).where(
            HelpRequest.help_request_id == help_request_id,
            HelpRequest.patient_id.in_(accessible_patient_ids(actor)),
        )
    )
    if request is None:
        raise HelpRequestNotFoundError("Help request not found.")

    patient, user = _patient_context(db, request)
    return request, patient, user


def _lock_help_request(
    db: Session,
    *,
    help_request_id: UUID,
    actor: User,
) -> HelpRequest:
    request = db.scalar(
        select(HelpRequest)
        .where(
            HelpRequest.help_request_id == help_request_id,
            HelpRequest.patient_id.in_(accessible_patient_ids(actor)),
        )
        .with_for_update()
    )
    if request is None:
        raise HelpRequestNotFoundError("Help request not found.")
    return request


def acknowledge_help_request(
    db: Session,
    *,
    help_request_id: UUID,
    actor: User,
) -> tuple[HelpRequest, bool]:
    request = _lock_help_request(
        db,
        help_request_id=help_request_id,
        actor=actor,
    )

    if request.status == HelpRequestStatus.ACKNOWLEDGED:
        return request, True

    if request.status != HelpRequestStatus.PENDING:
        raise HelpRequestTransitionConflictError(
            f"Cannot acknowledge a help request in "
            f"{request.status.value} status."
        )

    request.status = HelpRequestStatus.ACKNOWLEDGED
    request.acknowledged_by_user_id = actor.user_id
    request.acknowledged_at = utc_now()
    db.flush()
    return request, False


def resolve_help_request(
    db: Session,
    *,
    help_request_id: UUID,
    actor: User,
) -> tuple[HelpRequest, bool]:
    request = _lock_help_request(
        db,
        help_request_id=help_request_id,
        actor=actor,
    )

    if request.status == HelpRequestStatus.RESOLVED:
        return request, True

    request.status = HelpRequestStatus.RESOLVED
    request.resolved_by_user_id = actor.user_id
    request.resolved_at = utc_now()
    db.flush()
    return request, False
