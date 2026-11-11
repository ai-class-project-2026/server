from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import FilterMode, Role


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
