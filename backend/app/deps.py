from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Channel, Member, Role, User
from app.db.session import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def require_member(
    session: AsyncSession, workspace_id: int, user: User, role: Role | None = None
) -> Member:
    """404 for non-members so workspace ids don't leak; 403 if the role is too low."""
    member = await session.get(Member, (workspace_id, user.id))
    if member is None:
        raise _not_found("Workspace")
    if role is Role.owner and member.role is not Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner only")
    return member


async def get_owned(session: AsyncSession, model, obj_id: int, user: User):
    """Load a row that has workspace_id (or channel_id) and check membership."""
    obj = await session.get(model, obj_id)
    if obj is None:
        raise _not_found(model.__name__)
    ws_id = getattr(obj, "workspace_id", None)
    if ws_id is None:
        ws_id = (await session.get(Channel, obj.channel_id)).workspace_id
    try:
        await require_member(session, ws_id, user)
    except HTTPException:
        raise _not_found(model.__name__)
    return obj
