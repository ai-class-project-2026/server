dev:
	PYTHONPATH=ml/common uv run uvicorn app.main:app --reload --app-dir backend --reload-dir backend --reload-dir ml/common

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

openapi:
	JWT_SECRET=$${JWT_SECRET:-openapi-dump-only-placeholder-secret} uv run python -c "import json,sys; sys.path[:0]=['backend','ml/common']; from app.main import app; print(json.dumps(app.openapi()))" > openapi.json

seed:
	PYTHONPATH=backend:ml/common uv run python backend/seed.py
