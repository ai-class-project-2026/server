import asyncio
import datetime as dt
import logging
import weakref
from contextlib import contextmanager

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
    User,
)
from app.deps import Session, get_owned, require_member
from app.realtime import hub
from app.schedule import instruction
from app.schedule.pipeline import suggestion_out
from app.schemas import (
    EventDraft,
    EventIn,
    EventOut,
    EventUpdate,
    InstructionIn,
    SuggestionActionIn,
    SuggestionKind,
    SuggestionOut,
    SuggestionPayload,
    SuggestionResult,
    SuggestionRevised,
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
    # 위에서 읽은 뒤 누가 카드를 말로 고쳤을 수 있다: 차지한 뒤(쓰기 잠금 안)의 최신 내용으로 처리한다
    await session.refresh(s)

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


# --- 말로 고치기/추가하기 (기획서 6.1) ---

# 한 사람은 한 번에 하나만: 모델 호출이 30초까지 걸려서, 연타나 반복 호출이 모델 서버를 붙잡지 않게 한다.
# 같은 카드는 한 번에 하나씩 고친다: 둘이 동시에 고치면 뒤 사람 지시가 앞 사람이 고친 내용 위에 적용된다.
# ponytail: 프로세스 안 잠금이라 서버 워커 1개 기준 (realtime.Hub, pipeline._locks와 같은 전제)
_busy: set[int] = set()
_card_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


@contextmanager
def _one_at_a_time(user_id: int):
    if user_id in _busy:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Instruction already running")
    _busy.add(user_id)
    try:
        yield
    finally:
        _busy.discard(user_id)


def _instruction_error(e: Exception, kind: SuggestionKind | None = None) -> HTTPException:
    if isinstance(e, instruction.WantsCancel):
        detail = "Cancel the event in the calendar" if kind is SuggestionKind.update else "Ignore the card to drop it"
    elif isinstance(e, instruction.Invalid):
        detail = "Date is in the past" if "before the reference" in str(e) else "Invalid event"
    else:
        detail = "Instruction not understood"
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


async def _member_names(session, workspace_id: int) -> list[str]:
    return list(await session.scalars(select(User.name).join(Member).where(Member.workspace_id == workspace_id)))


@router.post("/suggestions/{suggestion_id}/revise", response_model=SuggestionRevised)
async def revise_suggestion(
    suggestion_id: int, body: InstructionIn, user: CurrentUser, session: Session
):
    """대기 중인 새 일정/일정 변경 카드를 한 줄 지시로 고친다. 같은 카드가 모든 팀원 화면에서 바뀐다(푸시 "suggestion").
    일정은 저장하지 않는다: 고친 뒤에도 팀원이 평소 버튼을 눌러야 한다."""
    s = await get_owned(session, AISuggestion, suggestion_id, user)
    ch = await session.get(Channel, s.channel_id)
    speaker, members = user.name, await _member_names(session, ch.workspace_id)
    with _one_at_a_time(user.id):
        lock = _card_locks.setdefault(suggestion_id, asyncio.Lock())
        async with lock:
            await session.refresh(s)  # 기다리는 사이 다른 사람이 고쳤거나 답했을 수 있다
            if s.action is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "Already handled")
            try:
                p = SuggestionPayload.model_validate(s.payload_json)
            except ValidationError:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid card")
            if p.kind is SuggestionKind.cancel:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Cancel cards cannot be revised")
            try:
                ev, changed = await instruction.revise(p.event, body.text, speaker, members, dt.datetime.now(dt.UTC))
            except (instruction.NotUnderstood, instruction.WantsCancel, instruction.Invalid) as e:
                raise _instruction_error(e, p.kind)
            # AI가 처음 낸 일정은 original_event로 남긴다 (버튼 결과와 함께 재학습 데이터, 기획서 5.2)
            new = {
                **s.payload_json,
                "event": ev.model_dump(mode="json"),
                "original_event": s.payload_json.get("original_event", p.event.model_dump(mode="json")),
            }
            # 답한 카드는 되살리지 않는다: 아직 아무도 답하지 않았을 때만 바꾼다 (act_on_suggestion의 차지와 같은 조건)
            done = await session.execute(
                update(AISuggestion)
                .where(AISuggestion.id == suggestion_id, AISuggestion.action.is_(None))
                .values(payload_json=new)
                .execution_options(synchronize_session=False)
            )
            if done.rowcount != 1:
                await session.rollback()
                raise HTTPException(status.HTTP_409_CONFLICT, "Already handled")
            await session.commit()
            await session.refresh(s)
            out = suggestion_out(s)
            # 잠금 안에서 보낸다: 연달아 고친 두 결과가 뒤바뀐 순서로 도착하지 않게
            await hub.broadcast(s.channel_id, "suggestion", out.model_dump(mode="json"))
    return SuggestionRevised(suggestion=out, changed=changed)


@router.post("/workspaces/{workspace_id}/events/parse", response_model=EventDraft)
async def parse_event(
    workspace_id: int, body: InstructionIn, user: CurrentUser, session: Session
):
    """일정 추가 창의 한 줄("내일 저녁 7시 도서관에서 팀 회의") → 칸을 채울 초안. 저장하지 않는다."""
    await require_member(session, workspace_id, user)
    speaker, members = user.name, await _member_names(session, workspace_id)
    with _one_at_a_time(user.id):
        try:
            fields = await instruction.draft(body.text, speaker, members, dt.datetime.now(dt.UTC))
        except (instruction.NotUnderstood, instruction.Invalid) as e:
            raise _instruction_error(e)
    return EventDraft(**fields, changed=list(fields))


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
