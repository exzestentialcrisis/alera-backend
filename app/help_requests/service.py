from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.help_requests.model import HelpRequest, HelpRequestStatus
from app.patients.model import ElderlyPatient


class HelpRequestConflictError(Exception):
    pass


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
