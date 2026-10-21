#!/usr/bin/env bash
# Step 4 (optional): the container test bed. Creates an Azure Container Registry and a Container Apps environment with the
# weather-app container app, and lets the runner VMs build, push and deploy it with their own identities (no secrets).
# Run after step 3, then push the weather-app repository and register its pipeline (see SETUP.md).
# Safe to run again: an existing resource is left as it is.
#
#   bash infra/4_weather_app.sh            # needs az login and infra/setup.env
#   DRY_RUN=1 bash infra/4_weather_app.sh  # prints the commands, changes nothing
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/setup.env"
DRY="${DRY_RUN:-0}"
: "${ACR:?set ACR in infra/setup.env (a unique registry name)}"
ACA_ENV="${ACA_ENV:-pipemedic-aca-env}"
APP="${WEATHER_APP:-weather-app}"
LOGS="${LOG_WORKSPACE:-pipemedic-logs}"

run() { if [ "$DRY" = "1" ]; then echo "+ $*"; else "$@"; fi; }
ask() { if [ "$DRY" = "1" ]; then echo "<${*: -1}>"; else az "$@"; fi; }
exists() { [ "$DRY" != "1" ] && az "$@" -o none 2>/dev/null; }
role() {  # role <principal id> <role name> <scope>
  if [ "$DRY" = "1" ]; then echo "+ az role assignment create --role '$2' (principal $1) --scope $3"; return; fi
  if [ -z "$(az role assignment list --assignee "$1" --role "$2" --scope "$3" --query '[0].id' -o tsv)" ]; then
    az role assignment create --assignee-object-id "$1" --assignee-principal-type ServicePrincipal --role "$2" --scope "$3" -o none
  fi
  echo "  role: $2"
}

run az account set --subscription "$SUBSCRIPTION"
run az provider register -n Microsoft.App --wait
run az provider register -n Microsoft.ContainerRegistry --wait
run az extension add --name containerapp --upgrade --yes --only-show-errors

echo "== container registry and log workspace"
exists acr show -n "$ACR" -g "$RG" || run az acr create -n "$ACR" -g "$RG" -l "$LOCATION" --sku Basic -o none
exists monitor log-analytics workspace show -n "$LOGS" -g "$RG" || run az monitor log-analytics workspace create -n "$LOGS" -g "$RG" -l "$LOCATION" -o none
WS_ID=$(ask monitor log-analytics workspace show -n "$LOGS" -g "$RG" --query customerId -o tsv)
WS_KEY=$(ask monitor log-analytics workspace get-shared-keys -n "$LOGS" -g "$RG" --query primarySharedKey -o tsv)

echo "== Container Apps environment and the app (a placeholder image until the pipeline's first deploy)"
exists containerapp env show -n "$ACA_ENV" -g "$RG" || run az containerapp env create -n "$ACA_ENV" -g "$RG" -l "$LOCATION" \
  --logs-workspace-id "$WS_ID" --logs-workspace-key "$WS_KEY" -o none
exists containerapp show -n "$APP" -g "$RG" || run az containerapp create -n "$APP" -g "$RG" --environment "$ACA_ENV" \
  --image mcr.microsoft.com/k8se/quickstart:latest --target-port 8000 --ingress external --min-replicas 0 --max-replicas 2 \
  --cpu 0.25 --memory 0.5Gi --system-assigned -o none

ACR_ID=$(ask acr show -n "$ACR" -g "$RG" --query id -o tsv)
APP_ID=$(ask containerapp show -n "$APP" -g "$RG" --query id -o tsv)
ENV_ID=$(ask containerapp env show -n "$ACA_ENV" -g "$RG" --query id -o tsv)
APP_PID=$(ask containerapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)

echo "== the app pulls from the registry with its own identity"
role "$APP_PID" AcrPull "$ACR_ID"
run az containerapp registry set -n "$APP" -g "$RG" --server "$ACR.azurecr.io" --identity system -o none

echo "== the runners build, push and deploy with theirs"
for i in $(seq 1 "$RUNNER_COUNT"); do
  vm="$VM_PREFIX$i"
  pid=$(ask vm show -n "$vm" -g "$RG" --query identity.principalId -o tsv)
  echo "-- $vm"
  role "$pid" AcrPush "$ACR_ID"
  role "$pid" Contributor "$APP_ID"     # update the app to a new image
  role "$pid" Reader "$ENV_ID"          # read the revision's state and logs
done

echo "== done. The app: https://$(ask containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
echo "Set ACR_NAME in the weather-app repository's pipelines/weather-app.yml to $ACR if it differs."
