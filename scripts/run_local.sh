#!/usr/bin/env bash
# Runs all four services locally: frontend :8000, orders :8001, payments :8002, inventory :8003
set -e
cd "$(dirname "$0")/../services"
for entry in frontend:8000 orders:8001 payments:8002 inventory:8003; do
  svc=$(echo "$entry" | cut -d: -f1)
  port=$(echo "$entry" | cut -d: -f2)
  (cd "$svc" && python -m uvicorn app.main:app --port "$port") &
done
wait
