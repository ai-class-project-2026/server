import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import (
    EventSource,
    EventStatus,
    FilterMode,
    ReportType,
    Role,
    SuggestionAction,
)


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginIn(BaseModel):
    id_token: str  # credential from Google Identity Services on the web


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
    text: str = Field(min_length=1, max_length=2000)


class MessageOut(BaseModel):
    id: int
    channel_id: int
    user_id: int
    user_name: str
    text: str  # raw; the web masks profanity_spans according to filter_mode
    created_at: dt.datetime
    is_profane: bool
    profanity_spans: list[list[int]]


class ReportIn(BaseModel):
    message_id: int
    type: ReportType


# --- calendar / todos ---


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    date: dt.date
    start: dt.time | None = None
    time_text: str | None = None
    location: str | None = None
    attendees: list[str] = []
    absent: list[str] = []
    note: str | None = None


class EventUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=100)
    date: dt.date | None = None
    start: dt.time | None = None
    time_text: str | None = None
    location: str | None = None
    status: EventStatus | None = None
    attendees: list[str] | None = None
    absent: list[str] | None = None
    note: str | None = None


class EventOut(EventIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    status: EventStatus
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

    title: str
    date: dt.date
    weekday: str | None = None
    start: dt.time | None = None
    time_text: str | None = None
    location: str | None = None
    status: EventStatus
    attendees: list[str] = []
    absent: list[str] = []
    note: str | None = None


class DialogueTurn(BaseModel):
    speaker: str
    text: str
    ts: dt.datetime | None = None


class ScheduleRequest(BaseModel):
    ref_datetime: dt.datetime
    participants: list[str]
    dialogue: list[DialogueTurn]
    existing_events: list[ScheduleEvent] = []


class ScheduleResponse(BaseModel):
    events: list[ScheduleEvent]
    model_version: str


class ModerateRequest(BaseModel):
    text: str = Field(max_length=2000)
    context: list[str] = []


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
