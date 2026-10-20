#!/usr/bin/env bash
# Collects CPU, memory and disk metrics and POSTs them to the PipeMedic health API.
# Reads API_URL and VAULT_URL from /etc/pipemedic-health.env. Runs every minute via systemd timer.
#
# The API key is not stored on disk. The VM's managed identity fetches it from Key Vault and the script keeps it
# for an hour in /run (memory only). Rotate the key in the vault and the VMs pick it up within the hour.
set -eu
. /etc/pipemedic-health.env

CACHE=/run/pipemedic-health.key
get_key() {
  if [ -f "$CACHE" ] && [ $(( $(date +%s) - $(stat -c %Y "$CACHE") )) -lt 3600 ]; then
    cat "$CACHE"; return
  fi
  local tok key
  tok=$(curl -fsS -m 10 -H "Metadata: true" \
    "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  key=$(curl -fsS -m 10 -H "Authorization: Bearer $tok" "$VAULT_URL/secrets/health-function-key?api-version=7.4" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['value'])")
  umask 077
  printf %s "$key" > "$CACHE"
  printf %s "$key"
}
FUNCTION_KEY=$(get_key)

HOST=$(hostname)

# CPU busy percent over a 1 second sample
read -r _ u1 n1 s1 i1 w1 q1 sq1 _ < /proc/stat
sleep 1
read -r _ u2 n2 s2 i2 w2 q2 sq2 _ < /proc/stat
BUSY=$(( (u2+n2+s2+q2+sq2) - (u1+n1+s1+q1+sq1) ))
IDLE=$(( (i2+w2) - (i1+w1) ))
CPU=$(awk -v b="$BUSY" -v i="$IDLE" 'BEGIN{t=b+i; printf "%.1f", (t>0)?100*b/t:0}')

MEM_TOTAL=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
MEM_AVAIL=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
MEM_PCT=$(awk -v t="$MEM_TOTAL" -v a="$MEM_AVAIL" 'BEGIN{printf "%.1f", 100*(t-a)/t}')

DISK_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')
DISK_FREE_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
LOAD1=$(cut -d' ' -f1 /proc/loadavg)
AGENT_UP=$(systemctl list-units --type=service --state=running 2>/dev/null | grep -c 'vsts.agent' || true)

PAYLOAD=$(printf '{"hostname":"%s","cpu_percent":%s,"memory_percent":%s,"memory_total_mb":%s,"disk_percent":%s,"disk_free_gb":%s,"load_1m":%s,"agent_service_running":%s}' \
  "$HOST" "$CPU" "$MEM_PCT" "$MEM_TOTAL" "$DISK_PCT" "$DISK_FREE_GB" "$LOAD1" "$([ "$AGENT_UP" -gt 0 ] && echo true || echo false)")

curl -fsS -m 20 -X POST "$API_URL/api/health" \
  -H "Content-Type: application/json" \
  -H "x-functions-key: $FUNCTION_KEY" \
  -d "$PAYLOAD"
echo
