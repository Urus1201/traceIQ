# viewer-backend

- FastAPI app (Python 3.11)
- Run: `uvicorn app.main:app --reload`
- Test: `pytest`
- Lint: `ruff .`, `black .`

## Sample data (SEG-Y files)

- Place sample `.sgy`/`.segy` files in the repository root folder `data/`.
- With docker-compose, `./data` on the host is mounted read-only at `/data` in the backend container.
- Use absolute container paths in API forms: `path=/data/example.sgy`.
- When running locally (not in Docker), pass the host file path directly.
