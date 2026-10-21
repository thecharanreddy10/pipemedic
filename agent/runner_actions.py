"""The fixed set of things PipeMedic may do on a runner, after a person approves.

Every action is a script written here, never by a model. The process actions only ever touch processes owned by the agent
user (the jobs it runs), never the Azure DevOps agent itself, ssh, or anything owned by root.
"""
import json
import re
import time

import config

AGENT_USER = "azureuser"
PROTECTED = re.compile(r"Agent\.Listener|Agent\.Worker|Runner\.Listener|Runner\.Worker|runsvc\.sh|vsts|sshd|systemd|waagent|walinuxagent|report_health",
                       re.IGNORECASE)

CLEAN_DISK = r"""
set -u
before=$(df -BM --output=avail / | tail -1 | tr -dc '0-9')
find /home/azureuser/azagent/_work -mindepth 1 -maxdepth 1 -type d ! -name _tool ! -name _tasks -exec rm -rf {} + 2>/dev/null
rm -rf /home/azureuser/.cache/pip /root/.cache/pip
rm -f /var/tmp/pm_fill
find /tmp -mindepth 1 -maxdepth 1 -mtime +1 -exec rm -rf {} + 2>/dev/null
if command -v docker >/dev/null && systemctl is-active --quiet docker; then docker system prune -af >/dev/null 2>&1; fi
after=$(df -BM --output=avail / | tail -1 | tr -dc '0-9')
echo "{\"free_mb_before\": $before, \"free_mb_after\": $after}"
"""

RESTART_AGENT = r"""
set -u
unit=$(systemctl list-units --type=service --all --no-legend 'vsts.agent*' | awk '{print $1}' | head -1)
if [ -z "$unit" ]; then echo '{"ok": false, "detail": "no agent service found"}'; exit 0; fi
systemctl restart "$unit"
sleep 8
echo "{\"ok\": true, \"unit\": \"$unit\", \"state\": \"$(systemctl is-active "$unit")\"}"
"""

RESTART_DOCKER = r"""
set -u
systemctl restart docker.socket docker.service
sleep 3
if docker info >/dev/null 2>&1; then echo '{"ok": true, "state": "running"}'; else echo "{\"ok\": false, \"state\": \"$(systemctl is-active docker)\"}"; fi
"""

# Read-only: the processes using the most CPU and memory, as JSON lines.
TOP_PROCESSES = r"""
ps -eo pid=,user:32=,pcpu=,pmem=,etimes=,comm=,args= --sort=-pcpu | head -25 | while read -r pid user cpu mem secs comm args; do
  unit=$(cat /proc/$pid/cgroup 2>/dev/null | head -1 | sed 's#.*/##')
  printf '{"pid": %s, "user": "%s", "cpu": %s, "mem": %s, "seconds": %s, "comm": "%s", "unit": "%s", "args": "%s"}\n' \
    "$pid" "$user" "$cpu" "$mem" "$secs" "$comm" "$unit" "$(echo "$args" | cut -c1-120 | tr -d '"\\')"
done
"""


def stop_process_script(pid: int, comm: str, unit: str) -> str:
    """Stops one process (or the transient group it runs in), after checking it is still the same process and still allowed."""
    unit_stop = f'systemctl stop "{unit}"' if unit.endswith(".scope") and unit.startswith(("run-", "pm-")) else ""
    return rf"""
set -u
now=$(ps -o comm= -p {pid} 2>/dev/null)
owner=$(ps -o user:32= -p {pid} 2>/dev/null)
if [ "$now" != "{comm}" ]; then echo '{{"ok": false, "detail": "the process is no longer running (or is a different one now)"}}'; exit 0; fi
if [ "$owner" != "{AGENT_USER}" ]; then echo '{{"ok": false, "detail": "the process is not owned by the agent user, so it is not stopped"}}'; exit 0; fi
{unit_stop or f"kill -TERM {pid}; sleep 5; kill -0 {pid} 2>/dev/null && kill -KILL {pid}"}
sleep 2
if kill -0 {pid} 2>/dev/null; then echo '{{"ok": false, "detail": "the process is still running"}}'; else echo '{{"ok": true, "detail": "stopped"}}'; fi
"""


def parse_json_lines(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def stoppable(p: dict) -> bool:
    """A process the agent may stop: one of the jobs the agent user runs, not the agent, a deployed app or a system service."""
    if str(p.get("unit", "")).endswith(".service"):  # a system service, or an app deployed on the runner
        return False
    return p.get("user") == AGENT_USER and not PROTECTED.search(f"{p.get('comm', '')} {p.get('args', '')} {p.get('unit', '')}")


def busiest(processes: list[dict], by: str = "cpu") -> dict | None:
    """The stoppable process using the most CPU (or memory), if it is using a meaningful share."""
    candidates = [p for p in processes if stoppable(p) and float(p.get(by, 0)) >= (20.0 if by == "cpu" else 10.0)]
    return max(candidates, key=lambda p: float(p.get(by, 0)), default=None)


def output_of(raw: str) -> str:
    return raw.split("[stdout]", 1)[-1].split("[stderr]", 1)[0].strip() if "[stdout]" in (raw or "") else (raw or "").strip()


def run_script(credential, vm: str, script: str, timeout_s: int = 120) -> str:
    from remediate import arm_run_command
    c = config.settings()
    return output_of(arm_run_command(c.subscription_id, c.resource_group, vm, script, credential, timeout_s))


def top_processes(credential, vm: str) -> list[dict]:
    return parse_json_lines(run_script(credential, vm, TOP_PROCESSES, 90))


def restart_vm(credential, vm: str, wait_s: int = 300) -> dict:
    """Restart a runner VM through Azure Resource Manager and wait for the restart to finish."""
    import requests
    from azure_auth import ARM_SCOPE
    c = config.settings()
    url = (f"https://management.azure.com/subscriptions/{c.subscription_id}/resourceGroups/{c.resource_group}/providers/"
           f"Microsoft.Compute/virtualMachines/{vm}/restart?api-version=2024-07-01")
    h = {"Authorization": "Bearer " + credential.get_token(ARM_SCOPE).token}
    r = requests.post(url, headers=h, timeout=60)
    if r.status_code not in (200, 202):
        return {"ok": False, "detail": f"restart refused: HTTP {r.status_code} {r.text[:150]}"}
    poll, deadline = r.headers.get("Azure-AsyncOperation") or r.headers.get("Location"), time.time() + wait_s
    while poll and time.time() < deadline:
        time.sleep(10)
        s = requests.get(poll, headers={"Authorization": "Bearer " + credential.get_token(ARM_SCOPE).token}, timeout=60)
        body = s.json() if s.status_code == 200 and s.text else {}
        if s.status_code == 200 and body.get("status", "Succeeded") in ("Succeeded", "Failed", "Canceled"):
            return {"ok": body.get("status", "Succeeded") == "Succeeded", "detail": f"restart {body.get('status', 'done').lower()}"}
    return {"ok": True, "detail": "restart requested"}
