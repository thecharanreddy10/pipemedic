#!/usr/bin/env bash
# Turns a fresh Ubuntu VM into a PipeMedic runner: the tools the pipelines need, the health report every minute, and the
# Azure DevOps agent as a service. Runs as root (infra/3_runners.sh sends it through az vm run-command, together with
# report_health.sh and the two systemd units, which it expects next to it or already in place).
#
#   bootstrap_runner.sh <health api url> <key vault url> <organization url> <pool> <agent name> <token>
#
# The token needs "Agent Pools: Read & manage" and is only used to register the agent; it is not stored.
set -euo pipefail
API_URL="$1"; VAULT_URL="$2"; ORG_URL="${3%/}"; POOL="$4"; AGENT_NAME="$5"; TOKEN="$6"
USER_NAME="azureuser"
AGENT_DIR="/home/$USER_NAME/azagent"

echo "== packages the pipelines and the health report use"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip curl jq

echo "== passwordless sudo for the agent user (the pipelines install services and read the journal)"
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-pipemedic
chmod 440 /etc/sudoers.d/90-pipemedic

echo "== health report every minute"
printf 'API_URL=%s\nVAULT_URL=%s\n' "$API_URL" "$VAULT_URL" > /etc/pipemedic-health.env
chmod 755 /usr/local/bin/report_health.sh
systemctl daemon-reload
systemctl enable --now pipemedic-health.timer

echo "== Azure DevOps agent"
if [ -f "$AGENT_DIR/.agent" ]; then
  echo "already configured: $(grep -h '"agentName"' "$AGENT_DIR/.agent" | tr -d ' ,')"
else
  url=$(curl -fsS -u ":$TOKEN" "$ORG_URL/_apis/distributedtask/packages/agent?platform=linux-x64&\$top=1&api-version=7.1" | jq -r '.value[0].downloadUrl')
  mkdir -p "$AGENT_DIR"
  curl -fsSL "$url" | tar xz -C "$AGENT_DIR"
  chown -R "$USER_NAME:$USER_NAME" "$AGENT_DIR"
  sudo -u "$USER_NAME" bash -c "cd '$AGENT_DIR' && ./config.sh --unattended --url '$ORG_URL' --auth pat --token '$TOKEN' \
    --pool '$POOL' --agent '$AGENT_NAME' --work _work --acceptTeeEula --replace"
  (cd "$AGENT_DIR" && ./svc.sh install "$USER_NAME" && ./svc.sh start)
fi
echo "== runner ready: $(hostname)"
