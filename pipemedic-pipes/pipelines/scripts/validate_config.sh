#!/usr/bin/env bash
# Checks that a deployment config file is present and valid JSON.
set -eu
file="$1"
echo "Validating $file..."
if [ ! -f "$file" ]; then
  echo "ERROR: required config file $file not found" >&2
  exit 1
fi
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$file"
echo "Config OK"
