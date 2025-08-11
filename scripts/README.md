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

---

# Offline Evaluation: Baseline vs LLM

`eval_iq.py` lets you compare the baseline parser with the LLM extractor without touching production code. It prints per-field diffs, simple aggregate stats, and (optionally) compares key numerics against the binary header as weak ground truth.

Requirements:
- Python 3.10+
- Optional: Azure OpenAI env vars set to enable LLM; otherwise runs baseline-only.

Usage examples:
```bash
# Pretty print results for up to 4 files in ./data
python3 scripts/eval_iq.py --limit 4

# Include fields where baseline and LLM agree (by default, only diffs are shown)
python3 scripts/eval_iq.py --limit 4 --show-agree

# Write JSON report
python3 scripts/eval_iq.py --limit 10 --format json --output eval.json

# CSV of per-field rows
python3 scripts/eval_iq.py --limit 10 --format csv --output eval.csv
```

Enable LLM (Azure OpenAI):
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY
- optional: AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT

The script reports counts of how many fields the LLM added, overrode, and agreed with baseline, plus correctness vs. binary header (if available via `segyio`).
