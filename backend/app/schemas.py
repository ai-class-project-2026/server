import datetime as dt
import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict, EmailStr, Field, StringConstraints, model_validator

from app.db.models import (
    EventSource,
    EventStatus,
    FilterMode,
    ReportType,
    Role,
    SuggestionAction,
)

_SURROGATE = re.compile(f"[{chr(0xD800)}-{chr(0xDFFF)}]")


def _has_surrogate(x) -> bool:
    if isinstance(x, str):
        return bool(_SURROGATE.search(x))
    if isinstance(x, dict):
        return any(_has_surrogate(k) or _has_surrogate(v) for k, v in x.items())
    if isinstance(x, list):
        return any(_has_surrogate(v) for v in x)
    return False


class BaseModel(_BaseModel):
    """모든 요청/응답 모델의 부모. 짝 없는 서로게이트 글자(U+D800~U+DFFF, JSON의 유니코드 이스케이프로
    보낼 수 있다)는 UTF-8로 저장할 수 없어 DB에서 500이 나므로, 여기서 한꺼번에 422로 막는다."""

    @model_validator(mode="before")
    @classmethod
    def _reject_lone_surrogates(cls, data):
        if _has_surrogate(data):
            raise ValueError("invalid unicode (lone surrogate)")
        return data


# 일정 글자 수 제한. AI 카드(ScheduleEvent)도 같은 값을 쓴다: 카드가 만든 일정이 그대로 저장되기 때문.
Name = Annotated[str, Field(max_length=50)]  # 사용자 이름(users.name)과 같은 길이
NameList = Annotated[list[Name], Field(max_length=200)]  # 참여자 수 제한(ScheduleRequest.participants)과 같은 값


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)  # 가입 때 제한과 같게: 터무니없이 긴 비밀번호는 해시하지 않는다


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ai_schedule_on: bool = False  # consent to chat analysis, asked at onboarding


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    ai_schedule_on: bool | None = None
    filter_mode: FilterMode | None = None
    filter_level: int | None = Field(None, ge=1, le=3)


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    invite_code: str
    ai_schedule_on: bool
    filter_mode: FilterMode
    filter_level: int


class InvitePreview(BaseModel):
    """초대 링크 페이지용 미리보기. 가입은 하지 않는다."""

    name: str
    ai_schedule_on: bool


class MemberOut(BaseModel):
    user_id: int
    name: str
    role: Role


# --- chat ---


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    name: str


class MessageIn(BaseModel):
    # 앞뒤 공백을 지운 뒤 검사한다: 공백만 있는 메시지는 422
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    # 재전송 방지용. 같은 (채널, 사용자, client_id)로 다시 오면 새로 저장하지 않고 기존 메시지를 돌려준다.
    client_id: str | None = Field(None, min_length=1, max_length=64)


class MessageOut(BaseModel):
    id: int
    channel_id: int
    user_id: int
    user_name: str
    text: str  # raw; the web masks profanity_spans according to filter_mode
    created_at: dt.datetime
    is_profane: bool
    profanity_spans: list[list[int]]
    client_id: str | None = None


class ReportIn(BaseModel):
    message_id: int
    type: ReportType


# --- calendar / todos ---


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    date: dt.date
    start: dt.time | None = None
    time_text: str | None = Field(None, max_length=100)
    location: str | None = Field(None, max_length=200)
    attendees: NameList = []
    absent: NameList = []
    note: str | None = Field(None, max_length=1000)


class EventUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=100)
    date: dt.date | None = None
    start: dt.time | None = None
    time_text: str | None = Field(None, max_length=100)
    location: str | None = Field(None, max_length=200)
    status: EventStatus | None = None
    attendees: NameList | None = None  # 값을 주려면 목록이어야 한다. 명시적 null은 라우터가 422로 막는다
    absent: NameList | None = None
    note: str | None = Field(None, max_length=1000)


class EventOut(BaseModel):
    """제한(max_length)을 일부러 걸지 않았다: 예전에 저장된 큰 값 때문에 목록 전체가 500이 나면 안 된다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    title: str
    date: dt.date
    start: dt.time | None = None
    time_text: str | None = None
    location: str | None = None
    status: EventStatus
    attendees: list[str]
    absent: list[str]
    note: str | None = None
    source: EventSource


class TodoIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    assignee_id: int | None = None
    due_date: dt.date | None = None


class TodoUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    assignee_id: int | None = None
    due_date: dt.date | None = None
    done: bool | None = None


class TodoOut(TodoIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    done: bool


# --- AI (plan 8.2, 10.4) ---


class ScheduleEvent(BaseModel):
    """One event as the schedule model outputs it (plan 8.2)."""

    title: str = Field(min_length=1, max_length=100)
    date: dt.date
    weekday: str | None = Field(None, max_length=10)
    start: dt.time | None = None
    time_text: str | None = Field(None, max_length=100)
    location: str | None = Field(None, max_length=200)
    status: EventStatus
    attendees: NameList = []
    absent: NameList = []
    note: str | None = Field(None, max_length=1000)


# 요청 크기 제한: 서버가 30턴 x 2,000자(Discord 니트로는 4,000자)까지 보내도 통과하고,
# 그보다 터무니없이 큰 입력만 막는다.
class DialogueTurn(BaseModel):
    speaker: str = Field(max_length=100)
    text: str = Field(max_length=4000)
    ts: dt.datetime | None = None


class ScheduleRequest(BaseModel):
    ref_datetime: dt.datetime
    participants: list[str] = Field(max_length=200)
    dialogue: list[DialogueTurn] = Field(max_length=100)
    existing_events: list[ScheduleEvent] = Field(default=[], max_length=200)


class ScheduleResponse(BaseModel):
    events: list[ScheduleEvent]
    model_version: str


class ModerateRequest(BaseModel):
    # 일정 API(DialogueTurn.text)와 같은 4,000자: Discord 니트로 메시지도 검사할 수 있어야 한다
    text: str = Field(max_length=4000)
    context: list[Annotated[str, Field(max_length=4000)]] = Field(default=[], max_length=10)  # 채팅은 3개를 쓴다


class ModerateResponse(BaseModel):
    is_profane: bool
    score: float
    spans: list[list[int]]
    restored: str
    model_version: str


class SuggestionKind(StrEnum):
    add = "add"
    update = "update"
    cancel = "cancel"


class SuggestionPayload(BaseModel):
    """Stored in ai_suggestions.payload_json and shown as the card."""

    kind: SuggestionKind
    event: ScheduleEvent
    event_id: int | None = None  # existing event for update/cancel
    message_ids: list[int] = []


class SuggestionOut(BaseModel):
    id: int
    channel_id: int
    payload: SuggestionPayload
    action: SuggestionAction | None
    created_at: dt.datetime


class SuggestionActionIn(BaseModel):
    action: SuggestionAction
    event: EventIn | None = None  # required for "edited": the user's corrected version


class SuggestionResult(BaseModel):
    suggestion: SuggestionOut
    event: EventOut | None


# --- 말로 고치기/추가하기 (기획서 6.1) ---


class InstructionIn(BaseModel):
    # 한 줄 지시. 제목(100자)·장소(200자)가 이 글에서 나오므로 장소 제한과 같은 200자
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SuggestionRevised(BaseModel):
    suggestion: SuggestionOut
    changed: list[str]  # 바뀐 칸 이름 (title, date, start, time_text, location, attendees, absent, note)


class EventDraft(BaseModel):
    """일정 추가 창에 채울 초안 (저장하지 않는다). 읽어 낸 칸만 값이 있고, 그 칸 이름이 changed에 있다."""

    title: str | None = None
    date: dt.date | None = None
    start: dt.time | None = None
    time_text: str | None = None
    location: str | None = None
    attendees: list[str] = []
    absent: list[str] = []
    note: str | None = None
    changed: list[str]
