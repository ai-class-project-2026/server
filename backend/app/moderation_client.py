"""MODERATION_URL이 있으면 욕설 판정을 다른 서버의 POST /moderate에 맡긴다 (같은 코드를 띄운 모델 서버).

모델 파일이 없는 기기에서도 백엔드를 돌릴 수 있게 하려는 것이다. 그 서버가 답하지 않거나 답이 이상하면
이 서버 안의 moderate()로 판정한다 (모델 경로가 없으면 사전만): 채팅은 멈추지 않는다.
"""

import logging

import httpx
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.moderation import ModerationResult, moderate
from app.schemas import ModerateResponse

log = logging.getLogger(__name__)
_transport = None  # 자체 점검에서 httpx.MockTransport로 바꾼다


async def check(text: str, context: list[str], level: int) -> ModerationResult:
    if settings.moderation_url:
        try:
            async with httpx.AsyncClient(timeout=settings.moderation_timeout, transport=_transport) as client:
                r = await client.post(
                    settings.moderation_url.rstrip("/") + "/moderate",
                    json={"text": text, "context": context, "level": level},
                    headers={"X-API-Key": settings.moderation_api_key},
                )
                r.raise_for_status()
                out = ModerateResponse.model_validate(r.json())
            if all(0 <= s < e <= len(text) for s, e in out.spans):  # 가릴 위치가 이 글 안이어야 한다
                return ModerationResult(**out.model_dump())
            log.warning("moderation server returned spans outside the text; judging locally")
        except (httpx.HTTPError, ValueError) as e:
            log.warning("moderation server failed (%s); judging locally", type(e).__name__)
    return await run_in_threadpool(moderate, text, context, level)


if __name__ == "__main__":  # PYTHONPATH=backend:ml/common python -m app.moderation_client
    import asyncio

    local = asyncio.run(check("아 씨발", [], 2))  # 주소가 없으면 이 서버 안에서 판정
    assert local.is_profane and local.spans == [[2, 4]]
    settings.moderation_url, settings.moderation_api_key = "http://model.invalid/", "k"
    seen = {}

    def reply(body, code=200):
        def handler(request):
            seen["key"], seen["path"], seen["json"] = request.headers["x-api-key"], request.url.path, request.content
            return httpx.Response(code, json=body)
        return httpx.MockTransport(handler)

    far = {"is_profane": True, "score": 0.9, "spans": [[0, 2]], "restored": "안녕", "model_version": "far"}
    _transport = reply(far)
    got = asyncio.run(check("안녕", ["앞"], 3))
    assert got.model_version == "far" and got.spans == [[0, 2]] and seen["key"] == "k" and seen["path"] == "/moderate"
    assert b'"level":3' in seen["json"].replace(b" ", b"")
    for bad in (reply(far, 500), reply({"nope": 1}), reply({**far, "spans": [[0, 9]]}), reply(far, 401)):
        _transport = bad  # 오류, 모양이 다른 답, 글 밖을 가리는 답, 키 거절 → 이 서버 안에서 판정
        assert asyncio.run(check("안녕", [], 2)).model_version != "far"
    _transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectTimeout("t")))
    assert asyncio.run(check("아 씨발", [], 2)).is_profane  # 연결이 안 돼도 채팅은 판정을 받는다
    print("ok")
