#!/usr/bin/env bash
# Installs a packaged service on this runner as a systemd service. Usage: deploy.sh <service> <port> <package.tgz>
set -euo pipefail
svc="$1"; port="$2"; package="$3"
SUDO="${PM_SUDO-sudo -n}"
app="/opt/pipemedic/$svc"

echo "Deploying $svc to $(hostname) on port $port"
stage="$(mktemp -d)"
tar xzf "$package" -C "$stage"
$SUDO mkdir -p "$app"
$SUDO cp -r "$stage"/. "$app"/
$SUDO python3 -m venv "$app/.venv"
$SUDO "$app/.venv/bin/pip" install --disable-pip-version-check --quiet -r "$app/requirements.txt"
echo "${BUILD_BUILDNUMBER:-unknown}" | $SUDO tee "$app/version.txt" >/dev/null

$SUDO tee "/etc/systemd/system/pipemedic-$svc.service" >/dev/null <<UNIT
[Unit]
Description=PipeMedic $svc service
After=network.target

[Service]
User=azureuser
WorkingDirectory=$app
Environment=INVENTORY_URL=http://127.0.0.1:8003
Environment=PAYMENTS_URL=http://127.0.0.1:8002
ExecStart=$app/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $port
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable "pipemedic-$svc" >/dev/null 2>&1
$SUDO systemctl restart "pipemedic-$svc"
echo "Service pipemedic-$svc restarted, version ${BUILD_BUILDNUMBER:-unknown}"
