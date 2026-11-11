from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth
from app.auth import CurrentUser
from app.db.models import User
from app.db.session import get_session
from app.schemas import GoogleLoginIn, LoginIn, SignupIn, TokenOut, UserOut

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
    # Same error for unknown email, wrong password, and Google-only accounts.
    if not (
        user
        and user.password_hash
        and auth.verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=auth.create_token(user.id))


@router.post("/google", response_model=TokenOut)
async def google_login(body: GoogleLoginIn, session: Session):
    claims = auth.verify_google_token(body.id_token)
    user = await session.scalar(select(User).where(User.google_id == claims["sub"]))
    if user is None:
        if not claims.get("email_verified"):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Google email not verified"
            )
        email = claims["email"].lower()
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(name=claims.get("name") or email.split("@")[0], email=email)
            session.add(user)
        else:
            # Our signup doesn't verify email, so someone may have pre-registered
            # this address. Google proved ownership: drop the password to lock them out.
            user.password_hash = None
        user.google_id = claims["sub"]
        await session.commit()
    return TokenOut(access_token=auth.create_token(user.id))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return user
