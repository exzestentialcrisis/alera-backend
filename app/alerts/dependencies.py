from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.users.model import User


async def get_development_actor(
    actor_id: Annotated[UUID, Header(alias="X-Alera-Actor-Id")],
    db: Session = Depends(get_db),
) -> User:
    """Resolve temporary development identity; this is not authentication."""
    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Actor user not found.",
        )
    return actor
