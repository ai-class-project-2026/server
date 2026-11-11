"""AI APIs (plan 10.4). Called by eval scripts and the Discord bot; the chat
flow calls the same functions in-process."""

import secrets
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
    if settings.ai_api_key and not secrets.compare_digest(
        x_api_key, settings.ai_api_key
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad X-API-Key")


router = APIRouter(tags=["ai"], dependencies=[Depends(_api_key)])


@router.post("/moderate", response_model=ModerateResponse)
async def moderate_api(body: ModerateRequest):
    r = await run_in_threadpool(moderate, body.text, body.context)
    return ModerateResponse(
        is_profane=r.is_profane,
        score=r.score,
        spans=r.spans,
        restored=r.restored,
        model_version=r.model_version,
    )


@router.post("/schedule", response_model=ScheduleResponse)
async def schedule_api(body: ScheduleRequest):
    return ScheduleResponse(
        events=await tracker.track(body), model_version=settings.schedule_model_version
    )
