"""운영자 화면: 가려진 메시지와 신고된 메시지를 모아 본다 (기획서 9.7 2번 "운영자 검수").
여러 팀의 메시지가 보이는 곳이라 settings.admin_emails에 있는 계정만 연다."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.auth import CurrentUser
from app.config import settings
from app.db.models import Channel, Message, Report, ReportType, User, Workspace
from app.deps import Session
from app.schemas import AdminModerationItem

router = APIRouter(tags=["admin"])


def _require_admin(user: User) -> None:
    # 운영자가 아니면 이런 주소가 있다는 것도 알리지 않게 404
    if user.email.lower() not in {e.lower() for e in settings.admin_emails}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/admin/moderation", response_model=list[AdminModerationItem])
async def moderation_feed(
    user: CurrentUser, session: Session, limit: int = Query(200, ge=1, le=500)
):
    """최근에 가려졌거나 신고된 메시지, 새것부터."""
    _require_admin(user)
    count = lambda t: func.sum(case((Report.type == t, 1), else_=0))  # noqa: E731
    reports = (
        select(
            Report.message_id,
            count(ReportType.false_positive).label("fp"),
            count(ReportType.missed).label("missed"),
        )
        .group_by(Report.message_id)
        .subquery()
    )
    rows = await session.execute(
        select(Message, User.name, Channel.name, Workspace.name, reports.c.fp, reports.c.missed)
        .join(User, User.id == Message.user_id)
        .join(Channel, Channel.id == Message.channel_id)
        .join(Workspace, Workspace.id == Channel.workspace_id)
        .outerjoin(reports, reports.c.message_id == Message.id)
        .where(or_(Message.is_profane, reports.c.message_id.is_not(None)))
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return [
        AdminModerationItem(
            id=m.id,
            created_at=m.created_at,
            workspace_name=ws_name,
            channel_name=ch_name,
            user_name=user_name,
            text=m.text,
            is_profane=m.is_profane,
            profanity_score=m.profanity_score,
            profanity_spans=m.profanity_spans,
            model_version=m.model_version,
            false_positive_reports=fp or 0,
            missed_reports=missed or 0,
        )
        for m, user_name, ch_name, ws_name, fp, missed in rows
    ]
