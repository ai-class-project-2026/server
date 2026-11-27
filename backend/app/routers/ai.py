"""AI APIs (plan 10.4). Called by eval scripts and the Discord bot; the chat
flow calls the same functions in-process."""

import secrets
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.moderation import moderate
from app.schedule import tracker
from app.schemas import (
    ModerateRequest,
    ModerateResponse,
    ScheduleRequest,
    ScheduleResponse,
)


def _api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    if not settings.ai_api_key:
        # 키를 안 정했으면 닫아 둔다. 개발할 때만 ALLOW_OPEN_AI_API=1 (make dev)로 연다.
        if not settings.allow_open_ai_api:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "AI_API_KEY is not configured"
            )
        return
    # 비ASCII 글자가 있어도 터지지 않게 바이트로 비교한다 (헤더는 latin-1로 읽힌다).
    if not secrets.compare_digest(
        x_api_key.encode("latin-1"), settings.ai_api_key.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad X-API-Key")


router = APIRouter(tags=["ai"], dependencies=[Depends(_api_key)])


@router.post("/moderate", response_model=ModerateResponse)
async def moderate_api(body: ModerateRequest):
    r = await run_in_threadpool(moderate, body.text, body.context, body.level, body.names)
    return ModerateResponse(**asdict(r))


@router.post("/schedule", response_model=ScheduleResponse)
async def schedule_api(body: ScheduleRequest):
    return ScheduleResponse(
        events=await tracker.track(body), model_version=settings.schedule_model_version
    )
