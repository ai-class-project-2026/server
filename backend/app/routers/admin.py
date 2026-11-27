"""운영자 화면: 가려진 메시지와 신고된 메시지를 모아 본다 (기획서 9.7 2번 "운영자 검수").
여러 팀의 메시지가 보이는 곳이라 X-Admin-Password 헤더가 settings.admin_password와 같을 때만 연다."""

import hmac
import time
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.config import settings
from app.db.models import Channel, Message, Report, ReportType, User, Workspace
from app.deps import Session
from app.schemas import AdminModerationItem

router = APIRouter(tags=["admin"])

# 비밀번호를 하나씩 넣어 보는 공격을 막는다: 최근 10분에 10번 틀리면 그동안은 맞는 비밀번호도 받지 않는다.
# ponytail: 서버 전체에 한 줄(IP 구분 없음)이라 누가 일부러 틀리면 운영자도 10분 동안 못 들어온다. 문제가 되면 IP별로 센다
MAX_FAILS, FAIL_WINDOW = 10, 600
_fails: deque[float] = deque()


def _require_admin(password: str | None) -> None:
    now = time.monotonic()
    while _fails and now - _fails[0] > FAIL_WINDOW:
        _fails.popleft()
    if len(_fails) >= MAX_FAILS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong passwords")
    # 401은 웹이 "로그인이 풀렸다"로 보고 로그아웃시키므로 403을 쓴다
    ok = bool(settings.admin_password) and hmac.compare_digest(
        (password or "").encode(), settings.admin_password.encode()
    )
    if not ok:
        _fails.append(now)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong admin password")


@router.get("/admin/moderation", response_model=list[AdminModerationItem])
async def moderation_feed(
    session: Session,
    x_admin_password: Annotated[str | None, Header()] = None,
    limit: int = Query(200, ge=1, le=500),
):
    """최근에 가려졌거나 신고된 메시지, 새것부터."""
    _require_admin(x_admin_password)
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
