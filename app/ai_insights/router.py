from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.ai_insights.facts import build_insight_fact_packet
from app.ai_insights.schema import CaregiverInsightResponse
from app.ai_insights.service import generate_caregiver_insights
from app.auth.dependencies import get_current_caregiver
from app.db.database import get_db
from app.patients.errors import PatientNotFoundError
from app.patients.service import get_patient
from app.users.model import User

router = APIRouter(prefix="/api/v1/patients", tags=["Caregiver insights"])


@router.get(
    "/{patient_id}/caregiver-insights",
    response_model=CaregiverInsightResponse,
    summary="Get fact-grounded caregiver insights for a patient",
    responses={
        404: {"description": "Patient not found in the actor's scope."},
    },
)
def read_caregiver_insights(
    patient_id: UUID,
    actor: Annotated[User, Depends(get_current_caregiver)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        patient_row = get_patient(db, actor, patient_id)
        fact_packet = build_insight_fact_packet(db, patient_row.patient)
        return generate_caregiver_insights(fact_packet)
    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
