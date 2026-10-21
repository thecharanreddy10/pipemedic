"""The runner's own logs around the time a pipeline failed.

Pipeline logs say what the job saw. The runner's system log, kernel log and agent log say what the machine saw: a process
killed for lack of memory, a disk that filled up, an agent that lost contact. collect() reads them from the VM with a
short read-only command through the Azure Resource Manager run-command API, and analyze() finds the known problems in
the text with rules, so a verdict never depends on the model alone.
"""
import re
from datetime import datetime, timedelta, timezone

import config

MARGIN_BEFORE_S = 0    # seconds read before the run started (the run before it may have ended just then)
MARGIN_AFTER_S = 30    # seconds read after it finished (a kernel or service message can arrive late)
MAX_OUTPUT = 3800       # the run-command API returns about 4 KB, so the script limits itself
AGENT_DIAG = "/home/azureuser/azagent/_diag"

STEP_LIMIT = "step-memory-limit (runner log)"
NOT_RUNNER_FAULT = {STEP_LIMIT}  # seen on the runner, but the runner is not to blame

# (flag, sections it may appear in (None = anywhere), pattern, plain-language message). The first rule that matches a line wins,
# so a step that went over its own memory limit is never also counted as the whole machine running out of memory.
RULES = [
    (STEP_LIMIT, None, r"memory cgroup out of memory",
     "A process went over the memory limit set for it and the kernel killed it. This says nothing about the machine's own memory."),
    # Only the kernel's machine-wide line. 'oom-kill', 'oom_reaper' and 'invoked oom-killer' are also written when a single
    # process goes over its own limit, so they cannot tell the two apart.
    ("out-of-memory (runner log)", None, r"(?<!memory cgroup )out of memory: kill(ed)? process",
     "The kernel killed a process because the machine ran out of memory."),
    ("disk-full (runner log)", None, r"no space left on device",
     "The disk filled up."),
    ("disk-io-errors (runner log)", None, r"i/o error|ext4-fs error|buffer i/o",
     "The disk reported I/O errors."),
    ("overloaded (runner log)", None, r"blocked for more than \d+ seconds",
     "Processes were blocked for a long time, so the machine was overloaded or its disk was stuck."),
    ("agent-lost (runner log)", ("agent-service", "agent-worker-log"),
     r"lost communication|could not connect|connection (was )?(reset|refused|timed out)|shutdown signal|has been shut down|disconnect",
     "The Azure DevOps agent lost contact with Azure DevOps or was shut down."),
    ("docker-stopped (runner log)", ("docker",), r"stopping docker\.service|stopped docker\.service|docker\.service: (?:failed|main process exited)"
     r"|stopped docker application container engine",
     "Docker was stopped on the runner."),
    ("agent-service-failed (runner log)", ("agent-service", "journal-warnings"),
     r"vsts\.agent[^\n]*(failed|exited|stopped)|main process exited",
     "The agent service failed or stopped."),
]


def build_script(start: datetime, finish: datetime) -> str:
    """The read-only shell script that prints the runner's logs for the run, in labelled sections."""
    fmt = "%Y-%m-%d %H:%M:%S"
    lo = (start - timedelta(seconds=MARGIN_BEFORE_S)).astimezone(timezone.utc).strftime(fmt)
    hi = (finish + timedelta(seconds=MARGIN_AFTER_S)).astimezone(timezone.utc).strftime(fmt)
    return f"""(
START="{lo}"; END="{hi}"
sec() {{ echo "### $1"; }}
sec journal-warnings
journalctl --utc --since "$START" --until "$END" -p warning --no-pager -q 2>/dev/null | cut -c1-220 | tail -n 25
sec kernel
journalctl --utc -k --since "$START" --until "$END" --no-pager -q 2>/dev/null | grep -iE 'out of memory|oom|killed process|no space|i/o error|blocked for more than|segfault' | cut -c1-220 | tail -n 8
sec agent-service
journalctl --utc -u 'vsts.agent*' --since "$START" --until "$END" --no-pager -q 2>/dev/null | grep -iE 'error|fail|exception|lost|cancel|timeout|disconnect|shutdown' | cut -c1-220 | tail -n 15
sec docker
journalctl --utc -u docker.service -u docker.socket --since "$START" --until "$END" --no-pager -q 2>/dev/null | grep -iE 'stop|start|fail|error' | cut -c1-220 | tail -n 8
sec agent-worker-log
for f in $(ls -t {AGENT_DIAG}/Worker_*.log 2>/dev/null | head -3); do
  if [ "$(date -u -r "$f" +%s)" -ge "$(date -u -d "$START" +%s)" ]; then grep -hiE 'error|exception|fail|killed|cancel' "$f" | cut -c1-220 | tail -n 8; fi
done
sec entries
journalctl --utc --since "$START" --until "$END" --no-pager -q 2>/dev/null | wc -l
sec now
uptime; free -m | sed -n 2p; df -h / | tail -1
) 2>&1 | head -c {MAX_OUTPUT}"""


def parse_output(raw: str) -> str:
    """Keep only what the script printed (the run-command API wraps it in [stdout] and [stderr] markers)."""
    text = raw or ""
    if "[stdout]" in text:
        text = text.split("[stdout]", 1)[1].split("[stderr]", 1)[0]
    return text.strip()


def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = "other"
    for line in text.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            out.setdefault(current, [])
        elif line.strip():
            out.setdefault(current, []).append(line.strip())
    return out


def analyze(text: str) -> list[dict]:
    """Known problems found in the runner's logs. One finding per kind, with the first matching line as evidence."""
    found, seen = [], set()
    for section, lines in sections(text).items():
        if section in ("now", "entries"):
            continue  # a snapshot of the machine after the run, not an event during it
        for line in lines:
            for flag, allowed, pattern, message in RULES:
                if flag in seen or (allowed and section not in allowed):
                    continue
                if re.search(pattern, line, re.IGNORECASE):
                    seen.add(flag)
                    found.append({"flag": flag, "message": message, "line": line[:220], "section": section,
                                  "runner_fault": flag not in NOT_RUNNER_FAULT})
                    break
    return found


def collect(credential, hostname: str, start_iso: str | None, finish_iso: str | None, run=None, now: datetime | None = None) -> dict:
    """Read and analyse the runner's logs for a run. Never raises: an unreachable VM just means no runner logs."""
    result = {"hostname": hostname, "available": False, "text": "", "findings": [], "error": None}
    if not hostname or not start_iso:
        result["error"] = "the runner or the run's start time is not known"
        return result
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finish_iso.replace("Z", "+00:00")) if finish_iso else (now or datetime.now(timezone.utc))
        window = f"{(start - timedelta(seconds=MARGIN_BEFORE_S)).strftime('%H:%M:%S')} to {(finish + timedelta(seconds=MARGIN_AFTER_S)).strftime('%H:%M:%S')} UTC"
        if run is None:
            from remediate import arm_run_command as run
        cfg = config.settings()
        raw = run(cfg.subscription_id, cfg.resource_group, hostname, build_script(start, finish), credential, 90)
    except Exception as e:
        result["error"] = str(e)[:200]
        return result
    text = parse_output(raw)
    count = journal_entries(text)
    if count == 0:
        # The journal here survives restarts, so an empty window means the machine was off or the journal was trimmed.
        return {**result, "window": window, "error": "the runner has no system log entries for that time (it was probably off)"}
    return {**result, "available": bool(text), "window": window, "text": text, "findings": analyze(text),
            "error": None if text else "the runner returned no log output"}


def journal_entries(text: str) -> int | None:
    """How many system log lines the runner had in the window, from the script's entries section (None if it is missing)."""
    lines = sections(text).get("entries") or []
    return int(lines[0]) if lines and lines[0].isdigit() else None
