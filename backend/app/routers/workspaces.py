import asyncio
import datetime as dt
import secrets

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.auth import CurrentUser
from app.db.models import Channel, Member, Role, User, Workspace
from app.deps import Session, require_member
from app.realtime import hub
from app.schemas import (
    InvitePreview,
    MemberOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)

router = APIRouter(tags=["workspaces"])


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, session: Session):
    ws = Workspace(
        name=body.name,
        ai_schedule_on=body.ai_schedule_on,
        # AI를 켠 시각: 이 시각 이후의 메시지만 AI가 본다
        ai_enabled_at=dt.datetime.now(dt.UTC) if body.ai_schedule_on else None,
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
    before = WorkspaceOut.model_validate(ws)
    was_on = ws.ai_schedule_on
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(ws, k, v)
    if ws.ai_schedule_on and not was_on:  # 꺼짐 → 켜짐: 이 시각부터의 대화만 분석한다
        ws.ai_enabled_at = dt.datetime.now(dt.UTC)
    elif not ws.ai_schedule_on:
        ws.ai_enabled_at = None
    await session.commit()
    after = WorkspaceOut.model_validate(ws)
    if after != before:  # 정말 바뀐 경우만: 다른 팀원 화면도 새 설정(가림 방식, 이름, AI)을 받게 한다
        await _broadcast(session, workspace_id, "workspace_updated", after.model_dump(mode="json"))
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


@router.get("/invites/{code}", response_model=InvitePreview)
async def preview_invite(code: str, user: CurrentUser, session: Session):
    """초대 페이지용: 가입하지 않고 워크스페이스 이름과 AI 사용 여부만 보여 준다."""
    ws = await session.scalar(select(Workspace).where(Workspace.invite_code == code))
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invite code")
    return ws


@router.post("/invites/{code}", response_model=WorkspaceOut)
async def join_by_invite(code: str, user: CurrentUser, session: Session):
    ws = await session.scalar(select(Workspace).where(Workspace.invite_code == code))
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid invite code")
    # 여러 탭이 동시에 들어와도 안 터지게: 이미 멤버면 그냥 넘어간다 (있으면 무시하는 INSERT).
    res = await session.execute(
        insert(Member)
        .values(workspace_id=ws.id, user_id=user.id, role=Role.member)
        .on_conflict_do_nothing()
    )
    await session.commit()
    if res.rowcount:  # 새로 들어온 경우만: 이 워크스페이스의 모든 채널에 알려 멤버 목록을 새로 받게 한다
        joined = MemberOut(user_id=user.id, name=user.name, role=Role.member).model_dump(mode="json")
        await _broadcast(session, ws.id, "member_joined", joined)
    return ws


async def _broadcast(session: Session, workspace_id: int, type_: str, data) -> None:
    """이 워크스페이스의 모든 채널 소켓에 알린다 (어느 채널 화면을 보고 있든 받게)."""
    channel_ids = await session.scalars(
        select(Channel.id).where(Channel.workspace_id == workspace_id)
    )
    await asyncio.gather(*(hub.broadcast(cid, type_, data) for cid in channel_ids))
