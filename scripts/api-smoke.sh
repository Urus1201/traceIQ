#!/usr/bin/env bash
# Simple smoke tests for the viewer-backend API using files from ./data
# Usage: BACKEND_URL=http://localhost:8000 scripts/api-smoke.sh [-n 5] [-u]
#  -n NUM   Limit number of files (default: 5)
#  -u       Also test upload mode for the first file

set -euo pipefail

BACKEND_URL=${BACKEND_URL:-http://localhost:8000}
LIMIT=5
TEST_UPLOAD=false
while getopts ":n:u" opt; do
  case $opt in
    n) LIMIT="$OPTARG" ;;
    u) TEST_UPLOAD=true ;;
    *) echo "Usage: BACKEND_URL=... $0 [-n NUM] [-u]" >&2; exit 2 ;;
  esac
done

echo "Backend: $BACKEND_URL"

echo -n "Health: "
code=$(curl -s -o /tmp/health.out -w "%{http_code}" "$BACKEND_URL/health")
echo "$code $(cat /tmp/health.out 2>/dev/null)"

shopt -s nullglob
files=(data/*.sgy data/*.segy)
if (( ${#files[@]} == 0 )); then
  echo "No .sgy/.segy files found in ./data" >&2
  exit 1
fi

count=0
for f in "${files[@]}"; do
  bn=$(basename "$f")
  printf '\n== %s ==\n' "$bn"
  # header/read via path (container sees /data)
  curl -s -X POST "$BACKEND_URL/header/read" -F path="/data/$bn" -o /tmp/read.json -w "code=%{http_code}\n" \
    | sed 's/.*/read -> &/'
  if [[ -s /tmp/read.json ]]; then
    jq -c '{encoding, count: (.lines|length), first: .lines[0], last: .lines[-1]}' /tmp/read.json || true
  fi
  # header/iq via path
  curl -s -X POST "$BACKEND_URL/header/iq" -F path="/data/$bn" -o /tmp/iq.json -w "code=%{http_code}\n" \
    | sed 's/.*/iq   -> &/'
  if [[ -s /tmp/iq.json ]]; then
    jq -c 'to_entries|map(select(.value != null))|map({k: .key, v: (if (.value|type)=="object" then .value.value else .value end)})' /tmp/iq.json || true
  fi

  # header/parse using lines from /header/read (baseline only; set use_llm=true to enable LLM fallback if configured)
  if [[ -s /tmp/read.json ]]; then
    jq -c '{lines: .lines, use_llm: true}' /tmp/read.json > /tmp/parse_body.json || true
    curl -s -X POST "$BACKEND_URL/header/parse" \
      -H "Content-Type: application/json" \
      -d @/tmp/parse_body.json \
      -o /tmp/parse.json -w "code=%{http_code}\n" | sed 's/.*/parse-> &/'
    if [[ -s /tmp/parse.json ]]; then
      jq -c '{header_non_null: (.header|to_entries|map(select(.value != null))|map(.key)), provenance_count: (.provenance|length)}' /tmp/parse.json || true
    fi
  fi

  count=$((count+1))
  [[ $count -ge $LIMIT ]] && break
done

if [[ "$TEST_UPLOAD" == "true" ]]; then
  first="${files[0]}"
  echo -e "\n-- Upload mode on: ${first} --"
  curl -s -X POST "$BACKEND_URL/header/read" -F file=@"$first" -o /tmp/up_read.json -w "code=%{http_code}\n" | sed 's/.*/read(up) -> &/'
  jq -c '{encoding, count: (.lines|length)}' /tmp/up_read.json || true
  curl -s -X POST "$BACKEND_URL/header/iq" -F file=@"$first" -o /tmp/up_iq.json -w "code=%{http_code}\n" | sed 's/.*/iq(up)   -> &/'
  jq -c '{datum: .datum.value, sample_interval_ms: .sample_interval_ms.value, record_length_ms: .record_length_ms.value}' /tmp/up_iq.json || true
  # parse (upload): reuse lines from the upload read result
  if [[ -s /tmp/up_read.json ]]; then
    jq -c '{lines: .lines, use_llm: true}' /tmp/up_read.json > /tmp/up_parse_body.json || true
    curl -s -X POST "$BACKEND_URL/header/parse" \
      -H "Content-Type: application/json" \
      -d @/tmp/up_parse_body.json \
      -o /tmp/up_parse.json -w "code=%{http_code}\n" | sed 's/.*/parse(up) -> &/'
    jq -c '{header_non_null: (.header|to_entries|map(select(.value != null))|map(.key)), provenance_count: (.provenance|length)}' /tmp/up_parse.json || true
  fi
fi

echo -e "\n-- Negative case (missing form fields) --"
curl -s -X POST "$BACKEND_URL/header/read" -o /tmp/neg.json -w "code=%{http_code}\n"
cat /tmp/neg.json || true
