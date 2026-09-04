from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.errors import AuthenticationError
from app.auth.security import decode_access_token
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.users.model import AccountStatus, User


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_actor(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_access_token(
            credentials.credentials, secret=settings.alera_jwt_secret or ""
        )
    except AuthenticationError as exc:
        raise unauthorized from exc
    actor = db.get(User, UUID(claims["sub"]))
    if actor is None or actor.account_status is not AccountStatus.ACTIVE:
        raise unauthorized
    return actor
