#!/usr/bin/env bash
# Removes anything a failure scenario left on the runner, and old images of this app. Always safe to run.
rm -f /var/tmp/pm_fill
systemctl is-active --quiet docker || sudo -n systemctl start docker || true
if systemctl is-active --quiet docker; then
  # keep the runner's disk in check: this app's images older than the last three, and unused build cache
  docker images --format '{{.Repository}}:{{.Tag}}' | grep '/weather-app:' | tail -n +4 | xargs -r docker rmi >/dev/null 2>&1 || true
  docker builder prune -f --filter until=24h >/dev/null 2>&1 || true
fi
echo "Runner clean: $(df -h / | tail -1 | awk '{print $4}') free, Docker $(systemctl is-active docker)"
