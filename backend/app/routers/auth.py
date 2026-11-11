from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.auth import CurrentUser
from app.db.models import User
from app.db.session import get_session
from app.schemas import LoginIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/signup", response_model=TokenOut, status_code=201)
async def signup(body: SignupIn, session: Session):
    user = User(
        name=body.name,
        email=body.email.lower(),
        password_hash=auth.hash_password(body.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return TokenOut(access_token=auth.create_token(user.id))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: Session):
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    # Same error for unknown email and wrong password: don't reveal which emails exist.
    if not (user and auth.verify_password(body.password, user.password_hash)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=auth.create_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
