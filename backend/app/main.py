from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import StatementError

from app.config import settings
from app.routers import ai, auth, calendar, chat, workspaces


class _TooBig(BaseException):
    """BaseException인 이유: FastAPI는 본문을 읽다 난 일반 Exception을 전부 400으로 바꿔 버린다."""


class BodyLimitMiddleware:
    """요청 본문이 max_bytes를 넘으면 413. Content-Length가 없는 요청(chunked)도 읽으면서 센다."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"")
        too_big = JSONResponse(
            {"detail": "Request body too large"}, status.HTTP_413_CONTENT_TOO_LARGE
        )
        if length.isdigit() and int(length) > self.max_bytes:
            return await too_big(scope, receive, send)

        total, started = 0, False

        async def limited_receive():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > self.max_bytes:
                    raise _TooBig
            return msg

        async def tracking_send(msg):
            nonlocal started
            started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooBig:
            if started:
                raise
            await too_big(scope, receive, send)


app = FastAPI(title="teamplmate")
app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
app.add_middleware(  # 마지막에 추가한 것이 가장 바깥: 413 응답에도 CORS 헤더가 붙는다
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (auth, workspaces, chat, calendar, ai):
    app.include_router(r.router, prefix=settings.api_prefix)


def _unprocessable(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status.HTTP_422_UNPROCESSABLE_CONTENT)


# 422 응답에 사용자가 보낸 값(input)을 그대로 싣지 않는다: 짝 없는 서로게이트 같은 값은 JSON으로 못 내보내
# 오류 응답 자체가 500이 되고, 큰 입력을 그대로 되돌려 보내는 것도 낭비다.
@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    errors = [
        {k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()
    ]
    return JSONResponse({"detail": errors}, status.HTTP_422_UNPROCESSABLE_CONTENT)


# 2^63 이상의 정수(경로·쿼리·본문 어디서든)는 SQLite가 못 받아서 OverflowError가 난다 → 422
@app.exception_handler(OverflowError)
async def _overflow(request: Request, exc: OverflowError):
    return _unprocessable("Number out of range")


@app.exception_handler(StatementError)  # SQLAlchemy가 위 오류를 감싸서 던지는 경우
async def _statement_error(request: Request, exc: StatementError):
    if isinstance(exc.orig, OverflowError | UnicodeEncodeError):
        return _unprocessable("Value out of range or not valid text")
    raise exc


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """예상 못 한 오류도 CORS 헤더를 붙여서 돌려준다 (없으면 브라우저가 500 내용을 읽지 못한다).
    이 핸들러는 CORS 미들웨어 바깥에서 돌기 때문에 직접 붙여야 한다."""
    headers = {}
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origins:
        headers = {
            "access-control-allow-origin": origin,
            "access-control-allow-credentials": "true",
            "vary": "Origin",
        }
    return JSONResponse(
        {"detail": "Internal Server Error"},
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers=headers,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
