#!/usr/bin/env bash
# Step 3: creates the runner VMs and makes each one a PipeMedic runner (health report every minute, Azure DevOps agent).
# Safe to run again: an existing VM is not recreated, and an agent that is already configured is left alone.
#
#   export AZDO_PAT=<token with Agent Pools: Read & manage>   # only used to register the agents
#   bash infra/3_runners.sh             # needs az login and infra/setup.env
#   DRY_RUN=1 bash infra/3_runners.sh   # prints the commands, changes nothing
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/setup.env"
DRY="${DRY_RUN:-0}"
: "${AZDO_PAT:?export AZDO_PAT first (a token with Agent Pools: Read & manage)}"

run() { if [ "$DRY" = "1" ]; then echo "+ $*"; else "$@"; fi; }
ask() { if [ "$DRY" = "1" ]; then echo "<${*: -1}>"; else az "$@"; fi; }

run az account set --subscription "$SUBSCRIPTION"
KV_ID=$(ask keyvault show -n "$KEY_VAULT" -g "$RG" --query id -o tsv)
API_URL="https://$FUNCTION_APP.azurewebsites.net"
VAULT_URL="https://$KEY_VAULT.vault.azure.net"
ORG_URL="https://dev.azure.com/$ADO_ORG"

# One script for the VM: the health report and its systemd units travel inside it, then the bootstrap runs.
payload="$(mktemp)"
trap 'rm -f "$payload"' EXIT
{
  echo "set -euo pipefail"
  for pair in "report_health.sh:/usr/local/bin/report_health.sh" "pipemedic-health.service:/etc/systemd/system/pipemedic-health.service" \
              "pipemedic-health.timer:/etc/systemd/system/pipemedic-health.timer"; do
    echo "echo '$(base64 -w0 "$ROOT/runner-health/${pair%%:*}")' | base64 -d > '${pair#*:}'"
  done
  sed '1d' "$ROOT/runner-health/bootstrap_runner.sh"   # its body, without the shebang
} > "$payload"

for i in $(seq 1 "$RUNNER_COUNT"); do
  vm="${VM_PREFIX}${i}"
  agent="pipemedic-agent-$(printf '%02d' "$i")"
  echo "== $vm (agent $agent)"
  if [ "$DRY" != "1" ] && az vm show -n "$vm" -g "$RG" -o none 2>/dev/null; then
    echo "  VM exists"
  else
    run az vm create -n "$vm" -g "$RG" -l "$LOCATION" --image Ubuntu2404 --size "$VM_SIZE" --admin-username "$ADMIN_USER" \
      --generate-ssh-keys --assign-identity '[system]' --public-ip-sku Standard -o none
  fi
  vm_id=$(ask vm show -n "$vm" -g "$RG" --query identity.principalId -o tsv)
  if [ "$DRY" = "1" ]; then echo "+ az role assignment create --role 'Key Vault Secrets User' ($vm) --scope $KV_ID"
  elif [ -z "$(az role assignment list --assignee "$vm_id" --role "Key Vault Secrets User" --scope "$KV_ID" --query '[0].id' -o tsv)" ]; then
    az role assignment create --assignee-object-id "$vm_id" --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$KV_ID" -o none
  fi
  if [ -n "${AUTO_SHUTDOWN_TIME:-}" ]; then run az vm auto-shutdown -n "$vm" -g "$RG" --time "$AUTO_SHUTDOWN_TIME" -o none; fi
  run az vm run-command invoke -n "$vm" -g "$RG" --command-id RunShellScript --scripts "@$payload" \
    --parameters "$API_URL" "$VAULT_URL" "$ORG_URL" "$ADO_POOL" "$agent" "$AZDO_PAT" --query "value[0].message" -o tsv
done
echo "Runners ready. They report health every minute; check $API_URL/api/health-history?minutes=5"
