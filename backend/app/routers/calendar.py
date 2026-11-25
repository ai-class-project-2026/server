import datetime as dt

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, update

from app.auth import CurrentUser
from app.db.models import (
    AISuggestion,
    Channel,
    Event,
    EventSource,
    EventStatus,
    Member,
    SuggestionAction,
    Todo,
)
from app.deps import Session, get_owned, require_member
from app.realtime import hub
from app.schedule.pipeline import suggestion_out
from app.schemas import (
    EventIn,
    EventOut,
    EventUpdate,
    SuggestionActionIn,
    SuggestionKind,
    SuggestionOut,
    SuggestionPayload,
    SuggestionResult,
    TodoIn,
    TodoOut,
    TodoUpdate,
)

router = APIRouter(tags=["calendar"])
log = logging.getLogger(__name__)


def _apply(obj, changes: dict) -> None:
    for k, v in changes.items():
        setattr(obj, k, v)


def _reject_null(changes: dict, required: tuple[str, ...]) -> None:
    """PATCH에서 null을 보내면 "지우기"인데, 꼭 있어야 하는 칸은 지울 수 없다. 저장 전에 422로 막는다."""
    bad = [k for k in required if k in changes and changes[k] is None]
    if bad:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"null not allowed: {', '.join(bad)}"
        )


# --- events ---


@router.get("/workspaces/{workspace_id}/events", response_model=list[EventOut])
async def list_events(
    workspace_id: int,
    user: CurrentUser,
    session: Session,
    start: dt.date | None = None,
    end: dt.date | None = None,
):
    """All statuses; the calendar usually hides cancelled ones."""
    await require_member(session, workspace_id, user)
    q = select(Event).where(Event.workspace_id == workspace_id)
    if start:
        q = q.where(Event.date >= start)
    if end:
        q = q.where(Event.date <= end)
    return await session.scalars(q.order_by(Event.date, Event.start))


@router.post(
    "/workspaces/{workspace_id}/events", response_model=EventOut, status_code=201
)
async def create_event(
    workspace_id: int, body: EventIn, user: CurrentUser, session: Session
):
    await require_member(session, workspace_id, user)
    ev = Event(workspace_id=workspace_id, **body.model_dump())
    session.add(ev)
    await session.commit()
    return ev


@router.patch("/events/{event_id}", response_model=EventOut)
async def update_event(
    event_id: int, body: EventUpdate, user: CurrentUser, session: Session
):
    ev = await get_owned(session, Event, event_id, user)
    changes = body.model_dump(exclude_unset=True)
    _reject_null(changes, ("title", "date", "status", "attendees", "absent"))
    _apply(ev, changes)  # explicit null clears start/location
    await session.commit()
    return ev


@router.delete("/events/{event_id}", status_code=204)
async def delete_event(event_id: int, user: CurrentUser, session: Session):
    await session.delete(await get_owned(session, Event, event_id, user))
    await session.commit()


# --- AI suggestion cards ---


@router.get("/channels/{channel_id}/suggestions", response_model=list[SuggestionOut])
async def list_pending_suggestions(
    channel_id: int, user: CurrentUser, session: Session
):
    """Cards nobody has answered yet, so they survive a page reload."""
    await get_owned(session, Channel, channel_id, user)
    rows = await session.scalars(
        select(AISuggestion)
        .where(AISuggestion.channel_id == channel_id, AISuggestion.action.is_(None))
        .order_by(AISuggestion.id)
    )
    out = []
    for s in rows:
        try:
            out.append(suggestion_out(s))
        except ValidationError:  # 망가진 카드 하나 때문에 목록 전체가 500이 되면 안 된다
            log.warning("skipping invalid stored suggestion %s", s.id)
    return out


@router.post("/suggestions/{suggestion_id}/action", response_model=SuggestionResult)
async def act_on_suggestion(
    suggestion_id: int, body: SuggestionActionIn, user: CurrentUser, session: Session
):
    """The button the user pressed is also the eval/retraining label (plan 5.2)."""
    s = await get_owned(session, AISuggestion, suggestion_id, user)
    if s.action is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already handled")
    if body.action is SuggestionAction.edited and body.event is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "edited needs event")

    # 동시에 여러 번 눌러도 한 번만 성공하도록, "아직 답 안 한 카드"일 때만 먼저 차지한다.
    # 이 UPDATE가 일정 변경과 같은 트랜잭션이라서, 진 요청은 아무것도 바꾸지 못하고 409가 된다.
    claimed = await session.execute(
        update(AISuggestion)
        .where(AISuggestion.id == suggestion_id, AISuggestion.action.is_(None))
        .values(action=body.action)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already handled")

    try:
        p = SuggestionPayload.model_validate(s.payload_json)
    except ValidationError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid card")
    fields = (
        body.event.model_dump()
        if body.event
        else p.event.model_dump(include=set(EventIn.model_fields))
    )
    ev = None
    if body.action is not SuggestionAction.ignored:
        if p.kind is SuggestionKind.add:
            ch = await session.get(Channel, s.channel_id)
            ev = Event(
                workspace_id=ch.workspace_id,
                source=EventSource.ai,
                source_message_ids=p.message_ids,
                **fields,
            )
            session.add(ev)
        else:
            ev = p.event_id and await session.get(Event, p.event_id)
            if not ev:
                raise HTTPException(status.HTTP_410_GONE, "Event was deleted")
            ch = await session.get(Channel, s.channel_id)
            if ev.workspace_id != ch.workspace_id:  # 다른 워크스페이스의 일정은 건드릴 수 없다
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
            if p.kind is SuggestionKind.update:
                _apply(ev, fields)
            else:
                ev.status = EventStatus.cancelled
    s.action = body.action  # 메모리의 값도 맞춰 둔다 (DB에는 위에서 이미 반영)
    # 같은 일정을 가리키던 다른 대기 카드 중 의미가 없어진 것도 같이 닫는다 (지우지 않고 "무시"로 남긴다).
    #  취소가 반영되면: 그 일정의 바꾸기·취소 카드 전부 (더는 확정된 일정이 아니다)
    #  바꾸기가 반영되면: 그 일정의 다른 바꾸기 카드 (예전 일정 값으로 채운 카드라, 받으면 방금 바꾼 내용을 되돌린다)
    #  바꾸기 뒤의 취소 카드는 그대로 둔다 (일정이 아직 있으니 여전히 고를 수 있다)
    # 이미 답한 카드는 action IS NULL 조건에 걸리지 않아 바뀌지 않는다. 위의 차지(UPDATE)와 같은 트랜잭션이다.
    moot = []
    if ev is not None and p.kind is not SuggestionKind.add:
        kinds = [SuggestionKind.update]
        if p.kind is SuggestionKind.cancel:
            kinds.append(SuggestionKind.cancel)
        moot = list(
            await session.scalars(
                update(AISuggestion)
                .where(
                    AISuggestion.action.is_(None),
                    AISuggestion.channel_id.in_(
                        select(Channel.id).where(Channel.workspace_id == ev.workspace_id)
                    ),
                    AISuggestion.payload_json["event_id"].as_integer() == ev.id,
                    AISuggestion.payload_json["kind"].as_string().in_(kinds),
                )
                .values(action=SuggestionAction.ignored)
                .returning(AISuggestion)
                .execution_options(synchronize_session=False)
            )
        )
    await session.commit()

    out = SuggestionResult(
        suggestion=suggestion_out(s),
        event=EventOut.model_validate(ev) if ev else None,
    )
    await hub.broadcast(
        s.channel_id, "suggestion_resolved", out.model_dump(mode="json")
    )
    for m in moot:  # 다른 팀원 화면에서도 답한 카드처럼 사라지게 같은 알림을 보낸다
        try:
            r = SuggestionResult(suggestion=suggestion_out(m), event=out.event)
        except ValidationError:  # 망가진 카드는 목록에도 안 나오니 알릴 것도 없다
            continue
        await hub.broadcast(m.channel_id, "suggestion_resolved", r.model_dump(mode="json"))
    return out


# --- todos ---


async def _check_assignee(session, workspace_id: int, assignee_id: int | None) -> None:
    if (
        assignee_id is not None
        and await session.get(Member, (workspace_id, assignee_id)) is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Assignee is not a member"
        )


@router.get("/workspaces/{workspace_id}/todos", response_model=list[TodoOut])
async def list_todos(workspace_id: int, user: CurrentUser, session: Session):
    await require_member(session, workspace_id, user)
    return await session.scalars(
        select(Todo)
        .where(Todo.workspace_id == workspace_id)
        .order_by(Todo.done, Todo.id)
    )


@router.post(
    "/workspaces/{workspace_id}/todos", response_model=TodoOut, status_code=201
)
async def create_todo(
    workspace_id: int, body: TodoIn, user: CurrentUser, session: Session
):
    await require_member(session, workspace_id, user)
    await _check_assignee(session, workspace_id, body.assignee_id)
    todo = Todo(workspace_id=workspace_id, **body.model_dump())
    session.add(todo)
    await session.commit()
    return todo


@router.patch("/todos/{todo_id}", response_model=TodoOut)
async def update_todo(
    todo_id: int, body: TodoUpdate, user: CurrentUser, session: Session
):
    todo = await get_owned(session, Todo, todo_id, user)
    changes = body.model_dump(exclude_unset=True)
    _reject_null(changes, ("title", "done"))
    if "assignee_id" in changes:
        await _check_assignee(session, todo.workspace_id, changes["assignee_id"])
    _apply(todo, changes)
    await session.commit()
    return todo


@router.delete("/todos/{todo_id}", status_code=204)
async def delete_todo(todo_id: int, user: CurrentUser, session: Session):
    await session.delete(await get_owned(session, Todo, todo_id, user))
    await session.commit()
