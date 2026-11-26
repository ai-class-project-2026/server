# 백엔드 이미지 (FastAPI + 욕설 ONNX 실행 환경). 모델 서버 배포에 쓴다: 저장소에 push하면 Dokploy가 이 파일로 다시 빌드한다.
# 모델 파일과 비밀 값은 이미지에 넣지 않는다: 모델 폴더는 /model 에, .env 는 /srv/.env 에 마운트한다.
FROM python:3.13-slim
RUN pip install --no-cache-dir uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group onnx --no-install-project
COPY alembic.ini ./
COPY backend ./backend
COPY ml/common ./ml/common
ENV PYTHONPATH=/srv/ml/common PYTHONDONTWRITEBYTECODE=1 DATABASE_URL=sqlite+aiosqlite:////data/app.db MODERATION_ONNX_PATH=/model
RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && exec uv run --no-sync uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000"]
