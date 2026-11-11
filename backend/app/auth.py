import datetime as dt
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.db.session import get_session

_hasher = PasswordHash.recommended()  # argon2


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _hasher.verify(password, hashed)


def create_token(user_id: int) -> str:
    exp = dt.datetime.now(dt.UTC) + dt.timedelta(days=settings.jwt_expire_days)
    return jwt.encode({"sub": str(user_id), "exp": exp}, settings.jwt_secret, "HS256")


async def user_from_token(token: str, session: AsyncSession) -> User:
    """Shared by HTTP and WebSocket (which passes the token as a query param)."""
    try:
        user_id = int(jwt.decode(token, settings.jwt_secret, ["HS256"])["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    return await user_from_token(creds.credentials, session)


CurrentUser = Annotated[User, Depends(current_user)]


def verify_google_token(token: str) -> dict:
    """Returns Google's claims (sub, email, email_verified, name)."""
    # Without an audience google-auth skips the check and accepts tokens issued
    # to any app, so refuse instead of verifying.
    if not settings.google_client_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google login not configured"
        )
    try:
        return id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.google_client_id
        )
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")
