"""Tables from plan 10.3. SQLite: list fields are JSON, enums are VARCHAR + CHECK."""

import datetime as dt
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    TypeDecorator,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Role(StrEnum):
    owner = "owner"
    member = "member"


class FilterMode(StrEnum):
    mask = "mask"  # 가리기
    warn = "warn"  # 경고
    notify_admin = "notify_admin"  # 관리자 알림


class EventStatus(StrEnum):
    proposed = "proposed"
    rejected = "rejected"
    confirmed = "confirmed"
    cancelled = "cancelled"


class EventSource(StrEnum):
    ai = "ai"
    manual = "manual"


class SuggestionAction(StrEnum):
    added = "added"
    edited = "edited"
    ignored = "ignored"


class ReportType(StrEnum):
    false_positive = "false_positive"
    missed = "missed"


class UTCDateTime(TypeDecorator):
    """SQLite는 시각을 UTC로 저장하고 시간대 없이(naive) 돌려준다.
    읽을 때 UTC 표시를 붙여서 API가 "...Z"로 내보내게 한다. 테이블 모양은 그대로다."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value.tzinfo is None:  # 시간대 없는 값은 이미 UTC로 본다
            return value
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _enum(cls: type[StrEnum]) -> Enum:
    return Enum(cls, native_enum=False, create_constraint=True, length=32)


def _now() -> Mapped[dt.datetime]:
    return mapped_column(UTCDateTime, server_default=func.now())


def _fk(target: str, **kw) -> Mapped[int]:
    return mapped_column(ForeignKey(target, ondelete="CASCADE"), index=True, **kw)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[dt.datetime] = _now()


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("filter_level BETWEEN 1 AND 3"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    invite_code: Mapped[str] = mapped_column(String(32), unique=True)
    # Off until the team consents to chat analysis during onboarding.
    ai_schedule_on: Mapped[bool] = mapped_column(default=False)
    filter_mode: Mapped[FilterMode] = mapped_column(
        _enum(FilterMode), default=FilterMode.mask
    )
    filter_level: Mapped[int] = mapped_column(default=2)
    # AI를 켠 시각. 이 시각 이후에 쓴 메시지만 AI가 본다 (켜기 전 대화는 동의 범위 밖). 꺼져 있으면 null.
    ai_enabled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)


class Member(Base):
    __tablename__ = "members"

    workspace_id: Mapped[int] = _fk("workspaces.id", primary_key=True)
    user_id: Mapped[int] = _fk("users.id", primary_key=True)
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.member)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _fk("workspaces.id")
    name: Mapped[str] = mapped_column(String(50))


class Message(Base):
    __tablename__ = "messages"
    # 같은 client_id로 다시 보내면(응답을 못 받고 재시도) 새로 저장하지 않는다. client_id가 없으면(null) 제한 없음.
    __table_args__ = (
        Index("uq_messages_client", "channel_id", "user_id", "client_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = _fk("channels.id")
    user_id: Mapped[int] = _fk("users.id")
    text: Mapped[str]
    created_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True
    )
    is_profane: Mapped[bool] = mapped_column(default=False)
    profanity_score: Mapped[float | None]
    profanity_spans: Mapped[list] = mapped_column(JSON, default=list)  # [[start, end]]
    model_version: Mapped[str | None] = mapped_column(String(32))
    client_id: Mapped[str | None] = mapped_column(String(64))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _fk("workspaces.id")
    title: Mapped[str] = mapped_column(String(100))
    date: Mapped[dt.date]
    start: Mapped[dt.time | None]  # null when the time isn't a clock time
    time_text: Mapped[str | None]  # original wording, e.g. "점심시간"
    location: Mapped[str | None]
    status: Mapped[EventStatus] = mapped_column(
        _enum(EventStatus), default=EventStatus.confirmed
    )
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    absent: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None]
    source: Mapped[EventSource] = mapped_column(
        _enum(EventSource), default=EventSource.manual
    )
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)


class AISuggestion(Base):
    __tablename__ = "ai_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = _fk("channels.id")
    payload_json: Mapped[dict] = mapped_column(JSON)
    action: Mapped[SuggestionAction | None] = mapped_column(
        _enum(SuggestionAction)
    )  # null = pending
    model_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = _now()


class Report(Base):
    __tablename__ = "reports"
    # 한 사람이 같은 메시지에 같은 종류의 신고는 한 번만.
    __table_args__ = (
        Index("uq_reports_once", "message_id", "user_id", "type", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = _fk("messages.id")
    user_id: Mapped[int] = _fk("users.id")
    type: Mapped[ReportType] = mapped_column(_enum(ReportType))
    created_at: Mapped[dt.datetime] = _now()


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _fk("workspaces.id")
    title: Mapped[str] = mapped_column(String(200))
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    due_date: Mapped[dt.date | None]
    done: Mapped[bool] = mapped_column(default=False)
