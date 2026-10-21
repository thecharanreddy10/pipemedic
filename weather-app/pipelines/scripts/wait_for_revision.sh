#!/usr/bin/env bash
# Waits until a Container Apps revision is running and healthy. If it is not, prints why (its state and the container's
# own logs) and fails. Usage: wait_for_revision.sh <app> <resource group> <revision> [timeout seconds]
set -uo pipefail
app="$1"; rg="$2"; rev="$3"; timeout_s="${4:-300}"
deadline=$((SECONDS + timeout_s))

state() {  # one line: provisioning, running, health, details (tab separated)
  az containerapp revision show -n "$app" -g "$rg" --revision "$rev" -o json 2>/dev/null |
    jq -r '[.properties.provisioningState, .properties.runningState, .properties.healthState, .properties.runningStateDetails] | map(. // "") | @tsv'
}

show_logs() {
  echo "--- How Container Apps reaches the app"
  port=$(az containerapp show -n "$app" -g "$rg" --query properties.configuration.ingress.targetPort -o tsv 2>/dev/null)
  echo "Ingress target port: Container Apps sends traffic and its start-up probe to port ${port:-unknown} of the container"
  echo "--- Container Apps system log for $app"
  az containerapp logs show -n "$app" -g "$rg" --type system --tail 30 --format json 2>&1 |
    jq -R -r 'fromjson? | "\(.TimeStamp // "") \(.Reason // "") \(.Msg // "")"' | tail -30
  echo "--- Container log for $rev"
  az containerapp logs show -n "$app" -g "$rg" --revision "$rev" --type console --tail 40 --format text 2>&1 | tail -40
  # A container that crashes leaves no replica to stream from; its output is kept in the environment's Log Analytics workspace.
  echo "--- Errors the container printed (Log Analytics; can lag a few minutes)"
  env_id=$(az containerapp show -n "$app" -g "$rg" --query properties.environmentId -o tsv 2>/dev/null)
  ws=$(az containerapp env show --ids "$env_id" --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv 2>/dev/null)
  if [ -n "$ws" ]; then
    az extension show -n log-analytics >/dev/null 2>&1 || az extension add -n log-analytics --allow-preview true --only-show-errors >/dev/null 2>&1
    for attempt in 1 2 3 4 5 6; do  # the log reaches the workspace a few minutes after the container wrote it
      errors=$(az monitor log-analytics query -w "$ws" -o tsv --only-show-errors --analytics-query \
        "ContainerAppConsoleLogs_CL | where RevisionName_s == '$rev' and Log_s has_any ('Error', 'Exception') | summarize by Log_s | take 8" \
        2>/dev/null | cut -f1 | head -8)
      [ -n "$errors" ] && break
      sleep 30
    done
    echo "${errors:-(nothing yet)}"
  fi
}

while [ $SECONDS -lt $deadline ]; do
  IFS=$'\t' read -r provisioning running health details <<< "$(state)"
  echo "$(date -u +%H:%M:%S) $rev: provisioning=${provisioning:-?} running=${running:-?} health=${health:-?} ${details:-}"
  if [ "${provisioning:-}" = "Failed" ] || [ "${health:-}" = "Unhealthy" ] || [ "${running:-}" = "Failed" ] \
     || [ "${running:-}" = "Degraded" ] || [ "${running:-}" = "ActivationFailed" ]; then
    sleep 60  # give the container's own log time to reach Log Analytics
    show_logs
    echo "##[error]Revision $rev did not start: running=${running:-?} health=${health:-?} ${details:-}"
    exit 1
  fi
  if [ "${health:-}" = "Healthy" ] && [ "${running:-}" = "Running" -o "${running:-}" = "RunningAtMaxScale" ]; then
    echo "Revision $rev is running and healthy"
    exit 0
  fi
  sleep 10
done
show_logs
echo "##[error]Revision $rev was not healthy after ${timeout_s}s: running=${running:-?} health=${health:-?} ${details:-}"
exit 1
