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

Zero-shot header -> JSON structuring
Converts the free-form header text into a clean, typed JSON (survey name, contractor, acquisition date, sample rate, coordinate system, etc.).
Prompt-engineering with GPT-4o or fine-tune a small LLM on <header, JSON> pairs; fallback rules for edge cases.