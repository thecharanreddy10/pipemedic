#!/usr/bin/env bash
# Failure scenarios for demos. Source this at the start of a step: source hooks.sh <phase> <service>
# With SCENARIO unset or "none" it does nothing. Each scenario acts in the one phase where the failure would really show up,
# so the step that fails is the normal one (for example "Install dependencies"), not a step made up for the demo.
phase="${1:-}"
svc="${2:-}"
scenario="${SCENARIO:-none}"
repo="${BUILD_SOURCESDIRECTORY:-.}"

note() { echo "Demo scenario in effect: $*"; }

if [ "$scenario" != "none" ]; then
  case "$phase:$scenario" in
    network:network-timeout)
      note "package index address changed to one that does not answer"
      export UPSTREAM_URL="http://10.255.255.1:8443/simple/"
      ;;
    install:dependency-missing)
      note "httpx pinned to a release that does not exist"
      sed -i 's/^httpx==.*/httpx==9.9.9/' requirements.txt
      ;;
    install:disk-full)
      free_mb=$(df --output=avail -BM / | tail -1 | tr -dc '0-9')
      note "filling the runner disk, leaving 60 MB"
      fallocate -l "$((free_mb - 60))M" /var/tmp/pm_fill
      # a timer removes the file even if the agent dies before the clean-up step runs
      sudo -n systemd-run --quiet --on-active=15min --unit="pm-fill-cleanup-$$" rm -f /var/tmp/pm_fill || true
      ;;
    lint:lint-error)
      note "an unused import added to app/main.py"
      sed -i '1i import json' app/main.py
      ;;
    test:test-failure)
      note "a defect introduced in the service code"
      git -C "$repo" apply "services/$svc/scenarios/test-failure.patch" 2>/dev/null || git -C "$repo" apply --directory=. "$repo/services/$svc/scenarios/test-failure.patch"
      ;;
    test:flaky-test)
      note "a test that depends on the build number"
      cat > tests/test_cache_flaky.py <<'PYTEST'
import os


def test_cache_warms_within_budget():
    # Passes on even build ids and fails on odd ones, like a timing-dependent test.
    assert int(os.getenv("BUILD_BUILDID", "0")) % 2 == 0, "cache was not warm in time"
PYTEST
      ;;
    benchmark:memory-hog)
      note "benchmark working set raised to 1500 MB, above its 600 MB budget"
      export BENCH_MEM_MB=1500
      ;;
    benchmark:cpu-starvation)
      note "another workload is using every CPU on the runner, and the full-size benchmark is run"
      # four busy processes in their own high-priority group; they stop by themselves after 200 s
      nohup sudo -n systemd-run --scope --quiet --unit=pm-hogs -p CPUWeight=1000 --uid="$(id -u)" \
        timeout 200 bash -c 'for i in 1 2 3 4; do (while :; do :; done) & done; wait' >/dev/null 2>&1 &
      export BENCH_REQUESTS=40000
      sleep 3
      ;;
    package:missing-config)
      note "deployment config removed"
      rm -f "config/${svc}.prod.json"
      ;;
    deploy:permission-denied)
      note "deploy step runs without elevated rights"
      export PM_SUDO=""
      ;;
  esac
fi
