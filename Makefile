dev:
	uv run uvicorn app.main:app --reload --app-dir backend

test:
	uv run pytest

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

openapi:
	uv run python -c "import json,sys; sys.path.insert(0,'backend'); from app.main import app; print(json.dumps(app.openapi()))" > openapi.json
