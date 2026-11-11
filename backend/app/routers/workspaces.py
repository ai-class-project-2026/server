import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db.models import Channel, Member, Role, User, Workspace
from app.db.session import get_session
from app.schemas import MemberOut, WorkspaceCreate, WorkspaceOut, WorkspaceUpdate

router = APIRouter(tags=["workspaces"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def require_member(
    session: AsyncSession, workspace_id: int, user: User, role: Role | None = None
) -> Member:
    """404 for non-members so workspace ids don't leak; 403 if the role is too low."""
    member = await session.get(Member, (workspace_id, user.id))
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    if role is Role.owner and member.role is not Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner only")
    return member


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, session: Session):
    ws = Workspace(
        name=body.name,
        ai_schedule_on=body.ai_schedule_on,
        invite_code=secrets.token_urlsafe(8),
    )
    session.add(ws)
    await session.flush()
    session.add_all(
        [
            Member(workspace_id=ws.id, user_id=user.id, role=Role.owner),
            Channel(workspace_id=ws.id, name="일반"),
        ]
    )
    await session.commit()
    return ws


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(user: CurrentUser, session: Session):
    return await session.scalars(
        select(Workspace)
        .join(Member)
        .where(Member.user_id == user.id)
        .order_by(Workspace.id)
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: int, user: CurrentUser, session: Session):
    await require_member(session, workspace_id, user)
    return await session.get(Workspace, workspace_id)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: int, body: WorkspaceUpdate, user: CurrentUser, session: Session
):
    await require_member(session, workspace_id, user, Role.owner)
    ws = await session.get(Workspace, workspace_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ws, k, v)
    await session.commit()
    return ws


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
async def list_members(workspace_id: int, user: CurrentUser, session: Session):
    await require_member(session, workspace_id, user)
    rows = await session.execute(
        select(Member.user_id, User.name, Member.role)
        .join(User)
        .where(Member.workspace_id == workspace_id)
        .order_by(Member.user_id)
    )
    return [MemberOut(user_id=uid, name=name, role=role) for uid, name, role in rows]


@router.post("/invites/{code}", response_model=WorkspaceOut)
async def join_by_invite(code: str, user: CurrentUser, session: Session):
    ws = await session.scalar(select(Workspace).where(Workspace.invite_code == code))
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invite code")
    if await session.get(Member, (ws.id, user.id)) is None:
        session.add(Member(workspace_id=ws.id, user_id=user.id))
        await session.commit()
    return ws
