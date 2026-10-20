# PipeMedic AI agent

Investigates failed Azure DevOps pipeline runs. For each failed run it:

1. Finds the failing step and reads its log (Azure DevOps Build API).
2. Finds which runner executed the job and its hostname (Agent Pools API).
3. Reads that runner's health history for the run window (`GET /api/health-history`).
4. Classifies the failure and combines it with runner health to decide where the fault is.
5. Prints the category, verdict, root cause, evidence and suggested fixes.

The verdict comes from rules, so it is repeatable and cannot be invented by a model.
If `ANTHROPIC_API_KEY` is set, Claude adds a short plain-language explanation of the same facts.

## Verdicts

| Category | Typical cause | Runner healthy | Runner stressed |
|---|---|---|---|
| test-failure (passed on same commit) | intermittent test | flaky | flaky |
| test-failure, script-failure, dependency, permissions, network | the code or its config | code-or-config | mixed |
| code-error | the code does not compile or import (syntax, bad import, name error) | code-or-config | mixed |
| static-analysis | a linter, formatter or type checker reported problems (ruff, mypy, flake8, black, eslint) | code-or-config | mixed |
| timeout | a hung step | code-or-config | runner |
| runner-environment | missing tool, no space | runner | runner |

## Run it

```bash
pip install -r requirements.txt
set AZDO_PAT=<token with Build: Read and Agent Pools: Read>   # Windows cmd
python pipemedic_agent.py --last 4          # four most recent failed runs
python pipemedic_agent.py --build-id 3      # one run
python pipemedic_agent.py --last 4 --json   # machine-readable
```

Optional: `ANTHROPIC_API_KEY` for explanations, `HEALTH_API` to point at another health API.
The names of your own resources (organization, subscription, Function App, storage, AI resource) are read from `agent/local.env`,
which `infra/1_azure.sh` writes (see `SETUP.md`). Defaults: project `pipemedic-pipes` (repo of the same name), pool `pipemedic-pool`.

## Flaky tests

A failed test run is called **flaky** when the same pipeline passed on the very same commit. The code did not
change between the two runs, so the failure cannot be a regression. If every run on that commit failed, it is
reported as a consistent failure instead.

A flaky verdict gets one automatic retry (even without runner health data) and a person is asked about quarantining the test.
If the retry fails too, a work item is opened.

Demo: run any `pipemedic-pipes` pipeline twice on one commit with the scenario `flaky-test`. It adds a test that fails on odd build
numbers and passes on even ones (a simulated intermittent failure), so one run fails and the other passes. Then:

```bash
python pipemedic_agent.py --build-id <the failed run> --remediate
```

## Predictive alerts

```bash
python pipemedic_agent.py --alerts          # no token needed, reads the public health API
```

Fits a line to each runner's recent disk and memory readings and warns when it is heading for 95%, with an estimate of the
minutes left (critical under 30, warning under 120). It also flags CPU above 85% for 5 samples in a row and a runner that
stopped reporting for 3 minutes. Small or noisy trends are ignored. `--watch` prints each new alert once, and the dashboard
shows them in its Predictive alerts section.

## Agent pool health

```bash
python pipemedic_agent.py --pool-status
```

Reports agents online and offline, busy and idle, utilization, jobs queued and how long they have waited,
average and maximum queue wait over recent jobs, and agent versions. It also explains problems in plain language:

| Finding | Meaning |
|---|---|
| `queue-with-idle-agents` | jobs wait while an agent is idle. Usually the free parallel-job limit or unmet demands |
| `capacity-saturated` | every agent is busy and jobs are queued. Add agents |
| `agent-offline`, `no-agents-online` | an agent or the whole pool cannot run jobs |
| `long-queue-wait` | the oldest job has waited over 5 minutes |
| `version-skew`, `update-available` | informational |

The dashboard shows the same data in its Agent pool section.

### Pool alerts by email (always-on watcher)

Every minute the watcher also checks the pool and each runner's health history (`pool_status.py`, `predict.py`) and emails
**once when a problem appears and once when it clears**. Informational findings never alert. A problem must be seen on two checks in
a row before it is sent and be gone for two checks before it is called resolved, so a one-minute blip sends nothing. A warning that
becomes critical is sent again once. Open problems are kept in Table Storage (`State`, partition `poolalerts`), and are saved
before the email is sent, so a failed send is never repeated every minute. Set `POOL_ALERTS=0` to turn it off. It uses the same
recipient and email settings as failure emails.

## Dashboard

```bash
pip install -r requirements.txt
streamlit run dashboard.py
```

Opens at http://localhost:8501 and uses your Azure sign-in (`az login`). Two pages:

| Page | What it shows |
|---|---|
| Pipelines | Pick an **organization**, a **project** and a **repository**. The runs of that repository's pipelines are listed with their status (succeeded, failed, running). Click a run to see **why it failed**: the cause in plain language, the failing step, the log lines that show it, what to do, how the runner was doing, the runner's own logs around the failure, and the AI's opinion when the agent made one. For a run that succeeded it just says so. |
| Runners | The health of every runner: now (healthy, under strain, not reporting), and over the last hour to 7 days with CPU, memory and disk charts and every time a runner stopped reporting. |

For a failed run the page shows the analysis the always-on agent made at the moment of failure when there is one, otherwise it works the cause out
from the logs and the runner's health on the spot. The earlier, larger dashboard (overview, failures list, chat, remediation settings) is kept as
`dashboard_legacy.py` (`streamlit run dashboard_legacy.py`); remediation will return to the new run page.

Without `az login`, set `AZDO_PAT` to an access token: it works for the one organization the token was made in, and the organization box becomes a text field.
Runner health only exists for runners that run the health reporter.

## Remediation (guardrailed)

```bash
python pipemedic_agent.py --build-id 9 --remediate        # show the plan, change nothing
python pipemedic_agent.py --build-id 9 --act              # run the allowlisted actions
python pipemedic_agent.py --watch --act                   # investigate and act on each new failure
```

Allowlist (the only things it does on its own):

| Action | When |
|---|---|
| `retry-run` | runner was stressed but has recovered, or its disk was just cleaned. Once per run, and a retry is never retried |
| `clean-workdir` | runner is low on disk and idle. Clears old job folders and the pip cache through `az vm run-command` |
| `create-work-item` | a code or config problem on a healthy runner. Assigned to whoever triggered the run |

Everything else is shown as "needs approval": installing a missing tool, restarting or scaling,
unrecognised causes. It also refuses to act when there is no runner health data, and stops
after 5 automatic actions per hour. Every executed action is appended to `remediation_log.jsonl`.

Token scopes for `--act`: Build (Read and execute), Work Items (Read and write), Agent Pools (Read).
`clean-workdir` also needs `az login` and, optionally, `AZ_SUBSCRIPTION` and `AZ_RESOURCE_GROUP`.

## Watch mode

```bash
python pipemedic_agent.py --watch --interval 30
```

Keeps running and prints a diagnosis for each new failed run as it appears.

## Stress a runner (demo)

Maxes out CPU on a VM for 7 minutes, then stops by itself:

```bash
az vm run-command invoke -g pipemedic-rg -n pipemedicvm1 --command-id RunShellScript --scripts "systemd-run --unit=pipemedic-stress --property=RuntimeMaxSec=420 bash -c 'for i in $(seq $(nproc)); do (while :; do :; done) & done; wait'"
```

Stop it early with `systemctl stop pipemedic-stress` on the VM. Run the timeout pipeline while it is active.

## Test

```bash
python -m pytest tests
```

## Demo idea

Run the timeout pipeline twice. First on a healthy runner: the agent blames the step.
Then start a CPU hog on the runner first (`stress --cpu 2`, or `yes > /dev/null &` twice)
and run it again: the agent blames the runner. Same failure, different cause, found by
correlating logs with runner health.

## AI (Azure AI Foundry)

The model is deployed in Azure AI Foundry (an AI Services resource, deployment `gpt-5-mini`; `FOUNDRY_ENDPOINT` names it). The agent signs in with
Microsoft Entra ID through your `az login`, so there is no API key. Your account needs the **Cognitive Services OpenAI User**
role on the resource. Settings are read from `FOUNDRY_ENDPOINT`, `FOUNDRY_DEPLOYMENT` and `AI_EFFORT`; set `AI_DISABLED=1` to
turn all AI features off.

| Feature | Where | What it does |
|---|---|---|
| AI second opinion | `--ai` on the command line, a button on each failure in the dashboard | The model reads the failing log and the runner health summary and gives its own category, cause, fixes and evidence |
| Ask PipeMedic | Dashboard tab | Chat over the loaded runs, health, pool and alerts, using read-only tools |

How it stays safe:
- **Rules decide, AI explains.** The verdict and every remediation action come from the rules. The AI cannot change either.
- **Quotes are verified.** Evidence lines must appear in the real log. If none do, confidence is lowered to "low".
- **Secrets are removed first.** Bearer tokens, passwords, keys and long opaque strings are redacted before a log leaves the machine.
- **Read-only chat.** The chat's tools only read data. There is no tool to retry, clean or create anything.
- **Disagreement is shown, not hidden.** If the AI's category differs from the rules, it says so.

The AI also covers causes the rules do not have, such as permissions problems.

```bash
az login
python pipemedic_agent.py --last 3 --ai
```

## Always-on watcher (runs in Azure)

The Function App runs the agent every minute (`watch_failures`, a timer trigger). For each new failed run it:

1. investigates it (rules, runner health, optional AI second opinion),
2. recalls similar past failures (failure memory),
3. drafts or opens an AI fix as a pull request (see below),
4. plans guardrailed actions, and only performs them when `ACT_ENABLED=1`,
5. emails the person who triggered the run,
6. records the diagnosis in Table Storage, and marks the run as handled **before** doing anything, so a crash can never repeat an email or a retry.

On its first start it records the existing failures as a baseline and sends nothing.

It signs in with the Function App's **managed identity**: no personal access token, no API key. The identity is a member of the Azure DevOps
project (Contributors) and has roles on the AI resource, Table Storage, Key Vault, the email service and the runner VMs.

| Setting | Meaning |
|---|---|
| `ACT_ENABLED` | 0 = dry run (default). 1 = allowlisted actions really run |
| `FIX_MODE` | `off`, `propose` (mention a drafted fix in the email), `open` (open real pull requests) |
| `MEMORY` | 1 = remember failures and say "this looks like run N" |
| `AI_IN_WATCHER` | 1 = add an AI second opinion to each diagnosis |
| `NOTIFY_ENABLED`, `NOTIFY_DEFAULT_TO` | email on/off, and the fallback recipient |

Deploy: `python health-api/deploy.py`. It builds a stamped package, uploads it, restarts the app and waits until `/api/version` reports the same stamp, which proves the new code is the code running.

## Failure trigger and runner logs

**Trigger.** An Azure DevOps service hook ("Build completed", failed and canceled) posts to `POST /api/ado-event`. The endpoint checks the payload,
puts the run on a storage queue (`pipemedic-events`) and answers at once; a queue-triggered function (`process_event`) then investigates it, so the
analysis starts within seconds of the failure. The one-minute timer stays as a safety net for anything the hook missed. Before investigating, a run is
**claimed** with an atomic table insert, so the hook, a repeated delivery and the timer can never handle the same run twice. Create the hooks with
`python scripts/create_service_hook.py --org <org> --project <project> --key-from-azure` (it only prints what it would do until you add `--apply`).

**Logs it reads.** The logs of up to four failed steps of the run (not only the first), the runner's health for the run window, and the **runner's own logs**
for the run window plus two minutes either side: the system log (warnings), the kernel log, the Azure DevOps agent service log and the agent's worker log.
They are read from the VM with a short read-only command through the Azure Resource Manager run-command API (the Function App's identity needs the
Virtual Machine Contributor role), limited to about 4 KB. Rules look for an out-of-memory kill, a full disk, disk I/O errors, an overloaded machine, a lost
or stopped agent; any of these counts like a health flag, so a code failure on such a runner becomes "mixed" and a timeout becomes "runner". The model is
given all of it (secrets removed first), must say in `runner_effect` whether the runner affected the failure, and may quote lines from any of the logs.
Quotes are still checked against the real text.

**When a worker is stopped.** A restart or a deployment can stop an investigation half way, and the cloud app has no log to read. So every
stage writes a note on the run (State table, partition `seen`), a running investigation writes a heartbeat every 10 seconds to the `Trace` table, and one
that is still running after 90 seconds writes where it is stuck. Every call that can stall (the email, the model, the runner logs) has a time limit. A run
whose note has not changed for 15 minutes is dealt with: if it stopped before it had done anything outside its own storage it is released **once** and
investigated again, otherwise it is closed with an error, because repeating it could send a second email or open a second work item.
`WATCH_TIMER=0` makes the one-minute timer skip failures (pool alerts keep running), which leaves only the hook.

**Limits.** The runners keep a persistent system journal, so logs from before a restart can still be read. A VM that is off cannot be read, and
a window with no journal entries (the machine was off, or the journal was trimmed) is reported as "no logs" rather than "no problems". `RUNNER_LOGS=0` turns this off. With it on, the timer investigates at most 2 runs per check (reading logs takes about 20 seconds each).

## Email notifications

Azure Communication Services Email, sent with the managed identity. The email says what failed, why, what to do, the runner's health,
any similar past failure, any AI fix, and a link to the run. It goes to the person who triggered the run (if that is an email address),
otherwise to `NOTIFY_DEFAULT_TO`. Azure keeps a suppression list: an address that bounces is blocked until it is removed.

## AI fixes as pull requests

```bash
python pipemedic_agent.py --build-id 13 --propose-fix     # show the proposal, change nothing
python pipemedic_agent.py --build-id 13 --open-pr         # open the pull request and re-run the pipeline on it
```

For test and script failures on a healthy runner, the model proposes full replacement files. Hard checks run **before** any branch exists:
only files under `services/`, never a test file, never a pipeline file, existing files edited and new files created, Python and JSON must parse,
at most 3 files, and a change may not remove `exit`, `raise` or `assert` (fixes must not weaken checks). Anything environment-specific in a new
file must be `REPLACE_ME`. The pipeline is re-run on the new branch, and its result is posted on the pull request. It is never merged
automatically, a pipeline may have up to three open fixes (never two for the same failure or touching the same file; set `MAX_OPEN_FIXES_PER_PIPELINE`), and at most three are opened per day.

## Owner settings (dashboard, Remediation tab)

Three preferences are kept in Table Storage (`State`, partition `settings`) and read by the watcher every minute, so a change
takes effect within a minute and needs no redeploy:

| Setting | Choices | Falls back to |
|---|---|---|
| AI fixes as pull requests | off, propose (describe in the email), open (write, verify and open a PR) | `FIX_MODE` app setting |
| Automatic actions allowed to run for real | any of `create-work-item`, `retry-run`, `clean-workdir` | `ACT_ENABLED` (all or none) |
| Send every alert to | an email address; empty means the person who triggered the run | `NOTIFY_OVERRIDE_TO` |

Each can be set **for one project** or **for all projects**. The page's "These settings apply to" choice picks which. A project's
own value wins; a project without one gets the all-projects value. Keys are `org~project::name` (Table Storage row keys cannot
contain a slash). Nothing here lets PipeMedic merge a pull request or run an action outside the allowlist.

Several open fixes per pipeline are allowed (up to `MAX_OPEN_FIXES_PER_PIPELINE`, default 3), but never two for the same
failure or two that change the same file, and at most three are opened per day.

Deploy with `python health-api/deploy.py`: it stamps the package, restarts the app, and waits until `/api/version` reports the stamp.

## Failure memory

Each diagnosed failure is embedded (`text-embedding-3-small`) and stored. A new failure is compared by cosine similarity (threshold 0.80;
real pairs of the same failure score 0.95 to 0.98 and different ones 0.5 to 0.63). When the same pipeline later passes on the same branch,
the old failure is marked resolved; an unmerged AI fix that passes shows as "fix ready". `python pipemedic_agent.py --build-memory` seeds memory from existing runs.

## Secrets

- The runners fetch the API key from **Key Vault** with their own managed identity and keep it for an hour in memory (`/run`). Nothing secret is on disk.
- The AI resource accepts Entra sign-in only (API keys are disabled).
- Rotate the health key by setting a new value for the Function key and the `health-function-key` secret.
