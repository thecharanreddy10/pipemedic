#!/usr/bin/env bash
# Removes anything a failure scenario left on the runner. Always safe to run.
rm -f /var/tmp/pm_fill
sudo -n systemctl stop pm-hogs.scope 2>/dev/null || true
echo "Runner clean: $(df -h / | tail -1 | awk '{print $4}') free"
