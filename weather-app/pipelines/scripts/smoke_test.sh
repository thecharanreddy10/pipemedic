#!/usr/bin/env bash
# Checks the deployed app answers, runs the build that was just deployed, and returns weather for a city.
# Usage: smoke_test.sh <base url> <expected version>
set -uo pipefail
base="$1"; want="$2"

for i in $(seq 1 12); do
  body=$(curl --silent --show-error --max-time 10 "$base/health") && echo "$body" | grep -q "\"version\":\"$want\"" && break
  echo "attempt $i: /health answered: ${body:-nothing}"
  body=""
  sleep 5
done
if [ -z "$body" ]; then
  echo "##[error]$base/health did not report version $want"
  exit 1
fi
echo "/health: $body"

code=$(curl --silent --output /tmp/weather.json --write-out '%{http_code}' --max-time 15 "$base/api/weather?city=London")
echo "/api/weather?city=London: HTTP $code $(head -c 300 /tmp/weather.json)"
if [ "$code" != "200" ]; then
  echo "##[error]The weather API answered HTTP $code"
  exit 1
fi
echo "Smoke test passed"
