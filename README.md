# traceIQ Monorepo

## Apps
- **viewer-backend**: FastAPI (Python 3.11)
- **viewer-ui**: React (Vite, TypeScript)

## Development
- Backend: `cd viewer-backend && uvicorn app.main:app --reload`
- Frontend: `cd viewer-ui && npm run dev`

## Docker Compose
- `docker compose up` serves:
  - React (Vite) on [localhost:5173](http://localhost:5173)
  - FastAPI on [localhost:8000](http://localhost:8000)
  - Redis, Postgres

## Lint/Test
- Backend: `pytest`, `ruff`, `black`
- Frontend: `npm run lint`, `npm run format`
