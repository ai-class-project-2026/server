import datetime as dt

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

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


def _apply(obj, changes: dict) -> None:
    for k, v in changes.items():
        setattr(obj, k, v)


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
    _apply(
        ev, body.model_dump(exclude_unset=True)
    )  # explicit null clears start/location
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
    return [suggestion_out(s) for s in rows]


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

    p = SuggestionPayload.model_validate(s.payload_json)
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
            ev = await session.get(Event, p.event_id)
            if ev is None:
                raise HTTPException(status.HTTP_410_GONE, "Event was deleted")
            if p.kind is SuggestionKind.update:
                _apply(ev, fields)
            else:
                ev.status = EventStatus.cancelled
    s.action = body.action
    await session.commit()

    out = SuggestionResult(
        suggestion=suggestion_out(s),
        event=EventOut.model_validate(ev) if ev else None,
    )
    await hub.broadcast(
        s.channel_id, "suggestion_resolved", out.model_dump(mode="json")
    )
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
    if "assignee_id" in changes:
        await _check_assignee(session, todo.workspace_id, changes["assignee_id"])
    _apply(todo, changes)
    await session.commit()
    return todo


@router.delete("/todos/{todo_id}", status_code=204)
async def delete_todo(todo_id: int, user: CurrentUser, session: Session):
    await session.delete(await get_owned(session, Todo, todo_id, user))
    await session.commit()
