import datetime as dt
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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


def token_claims(token: object) -> tuple[int, float]:
    """토큰에서 (사용자 id, 만료 시각[epoch 초])을 꺼낸다. 잘못된 토큰이면 401."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, ["HS256"], options={"require": ["exp"]}
        )
        return int(claims["sub"]), float(claims["exp"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


async def user_from_token(token: str, session: AsyncSession) -> User:
    """Shared by HTTP and WebSocket (WebSocket sends the token in its first frame)."""
    user_id, _ = token_claims(token)
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
