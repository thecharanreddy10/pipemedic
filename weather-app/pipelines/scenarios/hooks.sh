#!/usr/bin/env bash
# Failure scenarios that happen on the runner. Source this at the start of a step: source hooks.sh <phase>
# With SCENARIO unset or "none" it does nothing. Each scenario acts just before the step where the failure would really happen.
# Defects in the code, the Dockerfile or the pipeline are not here: they live on the demo/* branches as real commits.
phase="${1:-}"
scenario="${SCENARIO:-none}"

note() { echo "Demo scenario in effect: $*"; }

if [ "$scenario" != "none" ]; then
  case "$phase:$scenario" in
    build:docker-disk-full)
      free_mb=$(df --output=avail -BM / | tail -1 | tr -dc '0-9')
      note "filling the runner disk, leaving 20 MB, and building without the layer cache as a changed image would"
      fallocate -l "$((free_mb - 20))M" /var/tmp/pm_fill
      export DOCKER_BUILD_OPTS="--no-cache"
      # a timer removes the file even if the agent dies before the clean-up step runs
      sudo -n systemd-run --quiet --on-active=15min --unit="pm-fill-cleanup-$$" rm -f /var/tmp/pm_fill || true
      ;;
    build:docker-daemon-down)
      note "the Docker service on the runner is stopped"
      sudo -n systemctl stop docker.socket docker.service
      # started again by the clean-up step, or by this timer if the agent dies first
      sudo -n systemd-run --quiet --on-active=15min --unit="pm-docker-restart-$$" systemctl start docker || true
      ;;
  esac
fi
