# API Smoke Test

This folder contains a simple smoke test script for the viewer-backend API that exercises the running container against sample SEG‑Y files in `./data`.

## Script
- `api-smoke.sh` — Calls:
  - `GET /health`
  - `POST /header/read` (path and optional file upload)
  - `POST /header/iq` (path and optional file upload)
  - Negative case (missing form data)

## Requirements
- Backend running and reachable (default: `http://localhost:8000` as in `docker-compose.yml`)
- `curl` and `jq` installed on the host
- Sample files in `./data` (mounted into the container as `/data`)

## Usage
Environment variable:
- `BACKEND_URL` — Base URL of backend (default: `http://localhost:8000`)

Flags:
- `-n NUM` — Limit the number of files to test (default: 5)
- `-u` — Also test file upload mode for the first file

Examples:
```bash
# Test 4 files via path mode and also run upload mode for the first one
BACKEND_URL=http://localhost:8000 ./scripts/api-smoke.sh -n 4 -u

# Use defaults (5 files, path mode only)
./scripts/api-smoke.sh
```

The script prints HTTP status codes and a compact JSON summary of fields detected by `/header/iq`. If a value looks noisy (e.g., contractor lines from legal disclaimers), consider refining the heuristics in `viewer-backend/app/iq_parser.py`.
