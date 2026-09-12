from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_caregiver
from app.core.time import utc_now
from app.db.database import get_db
from app.devices.model import CaregiverPushDevice
from app.users.model import User


class SafeValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default error body echoes invalid inputs, including tokens.
                raise HTTPException(422, "Invalid device token request.") from None

        return handler


router = APIRouter(
    prefix="/api/v1/devices", tags=["devices"], route_class=SafeValidationRoute
)
Token = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9_:.-]+$"
    ),
]


class TokenDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Token


class TokenRegistration(TokenDelete):
    platform: Literal["ANDROID"]


def _persist(db, statement):
    try:
        db.execute(statement)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Device registration unavailable.") from None
    return {"status": "ok"}


@router.post("/fcm-token")
def register_token(
    payload: TokenRegistration,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    now = utc_now()
    statement = insert(CaregiverPushDevice).values(
        user_id=actor.user_id,
        fcm_token=payload.token,
        platform=payload.platform,
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[CaregiverPushDevice.fcm_token],
        set_={
            "user_id": actor.user_id,
            "platform": payload.platform,
            "updated_at": now,
            "last_seen_at": now,
        },
    )
    return _persist(db, statement)


@router.delete("/fcm-token")
def remove_token(
    payload: TokenDelete,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    return _persist(
        db,
        delete(CaregiverPushDevice).where(
            CaregiverPushDevice.user_id == actor.user_id,
            CaregiverPushDevice.fcm_token == payload.token,
        ),
    )
