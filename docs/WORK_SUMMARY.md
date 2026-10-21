# PipeMedic: work summary

What was built, what went wrong along the way and how it was fixed, and what was checked on the live system.
Run numbers refer to the `pipemedic-pipes` project in Azure DevOps.

## Where it started

The team had a working investigator (failed run → failing step's log → runner → verdict), a runner health collector writing
to Azure Table Storage every minute, a health API on an Azure Function App, guardrailed remediation, an AI second opinion,
email, a dashboard and an accuracy benchmark. The test suite had not been run on this machine, the runners were stopped,
the agent only looked for failures on a one-minute timer, pool problems were reported only when someone asked, and nothing
could fix a failure.

## What was added

| Area | What it does now |
|---|---|
| **Pool alerts** | The watcher checks the agent pool every minute and emails once when an agent goes offline, the queue is stuck, or a runner's disk or memory is heading for full, and again when the problem clears. |
| **Event-driven investigation** | An Azure DevOps service hook (build completed) calls `POST /api/ado-event`, which queues the run. A queue-triggered function investigates it at once. The one-minute timer stays as a safety net. A run is claimed atomically, so it is investigated once. |
| **Runner logs** | The agent reads the runner's own system, kernel and agent logs around the failure through Azure run command, read-only. It tells a step's memory limit (the step's fault) apart from the whole machine running out of memory (the runner's fault). |
| **AI second opinion** | Sees every failed step and the runner's logs; every quote it gives must appear in the real logs. The verdict still comes from rules. |
| **Reliability** | Time limits for each stage, a stall watchdog, a heartbeat, a per-run trace table, and releasing a stalled run once so it is retried. |
| **Dashboard** | Choose organization, project and repository; click a pipeline, then a run; see why it failed, which runners ran it, and the fix plans. A Runners page shows live health and history, keeping peaks. |
| **Realistic pipelines** | A separate project and repository, `pipemedic-pipes`: four FastAPI services with lint, test, benchmark, package, deploy to a runner and smoke test, ten failure scenarios chosen with a pipeline parameter, and `demo/*` branches with real committed defects. |
| **Setup kit** | `infra/1_azure.sh`, `infra/2_devops.py`, `infra/3_runners.sh` and `SETUP.md` create everything in a new subscription and organization. Resource names live in `agent/local.env`, not in the code. |
| **Fix with approval** | For each failure the agent prepares fix plans. Nothing changes until a person approves one in the dashboard or through a signed link in the email. See below. |

### Fix with approval

| Failure | Plan offered | After approval |
|---|---|---|
| Failing test, lint error, bad dependency pin, missing file, code error | A pull request with the AI's change, shown as a diff | Branch and pull request into the failed run's branch, a proving run on it, then an email with the result. Never merged automatically. |
| The cause is in a pipeline file (for example a step memory limit) | A pull request changing the pipeline file, marked as such | As above |
| Disk full | Clean old job folders, caches and temp files | The clean-up runs on the VM, then the pipeline is re-run |
| A runaway job using the CPU or memory | Stop that named job | Stopped, then re-run. Only jobs run by the agent user; never the agent, ssh, system services or deployed apps |
| Agent down | Restart the agent service | Restart, wait until it is online, re-run |
| Runner still unhealthy | Restart the VM (last resort) | Restart through Azure, wait for the agent, re-run |
| Flaky test, network, permissions | Re-run | Re-run |
| The AI is unsure or its change breaks a rule | A job for a person, with the reason | Nothing automatic |

Every plan states its steps, how the result is checked, how to undo it, and the risk. A plan is decided once, expires after
24 hours, and approving one withdraws the other options for that run. The email link only opens a page showing the plan;
the decision is a button press on that page, so a mail scanner opening the link approves nothing.

## Problems found and fixed

| Problem | What it caused | Fix |
|---|---|---|
| Azure DevOps sends a minimal hook payload (only the run's id and address) | Every hook event was ignored | The parser accepts the minimal payload and reads the run's details itself |
| Pipelines created through the API need permission to use the pool | New pipelines stayed queued forever | The setup authorizes each pipeline on the pool |
| App setting changes restart the Function App mid-investigation | Runs stayed "processing" with no email | Stage time limits, stall watchdog, heartbeat, trace, and releasing a stalled run once |
| Classifier misses: a flaky test across different parameters, exit code 137, a step timeout, a neighbouring run's log lines | Wrong or missing verdicts | Rules fixed; all ten scenarios now diagnosed correctly |
| A step hitting its own memory limit was blamed on the runner | "Runner problem" while the runner had memory to spare | Step memory limit and machine-wide out-of-memory are told apart; the AI prompt explains the difference |
| Runner logs were assumed lost at restart | Wrong advice in the diagnosis | The journal is persistent; code and docs corrected |
| Vague wording for memory and disk problems, and "not recognised" for network failures | Confusing emails | Wording specific to each kind of problem |
| The runner chart averaged readings | A 100% CPU peak shown on the card did not appear on the chart | Charts plot the peak of each interval |
| Dashboard rows could not be clicked | No way to open a pipeline's runs | Buttons for pipelines and runs |
| The AI gave a file path relative to the service folder | The fix for a missing config file was rejected | The model is told to give paths from the repository root; run 78 now proposes `services/frontend/config/frontend.prod.json` |
| `ps` shortens user names to 8 characters (`azureus+`) | No runaway job could ever be stopped | Full user names are read; deployed app and system services are protected |
| The limit of 3 pull requests a day also blocked drafts | A failure got no fix plan after three tests | The limit applies only to pull requests opened without a person asking |
| Other plans for a run stayed approvable after one was approved | Two fixes could run for one failure | Approving a plan withdraws the others |
| The result email's send status was not kept | Could not confirm the result emails were sent | The status is saved on the plan |

## Checked on the live system

| What | Result |
|---|---|
| Ten failure scenarios across the four pipelines | All ten diagnosed correctly |
| Hook to email | About 20 to 85 seconds from the failure to the email |
| Demo branches (runs 63 to 67) | Test failure, lint error, missing dependency version, missing config and step memory limit all diagnosed correctly |
| Run 68, test failure | Approved; pull request into `demo/test-failure` changing `STOCK[item] + 1` to `STOCK[item]`; proving run 72 passed |
| Run 69, step memory limit | Approved; pull request raising `MemoryMax` from 60M to 256M in the pipeline template; proving run 74 passed |
| Run 70, disk full | Approved; clean-up on the runner, then re-run 73 passed; the other options for run 70 were withdrawn |
| Run 71, network timeout | Rejected through the email link's page. Opening the link changed nothing; a forged link (403) and a second decision (409) were refused |
| Stopping a runaway job | A busy test job on a runner was found and stopped, and nothing was left running |
| Runs 75 and 78 | Plans waiting for approval, with Approve and Reject in the dashboard |

297 automated tests pass. On 137 real failures from an open-source project, the rules name the cause correctly in 137 and
the rules with the AI's refinement in 135.

## Not done yet, and known limits

- Restarting the agent and restarting a VM have tests but have not been run on a live runner.
- Whether the result emails for runs 68 to 70 reached the inbox was not confirmed; from now on each plan records its email status.
- A plan expires after 24 hours but is not withdrawn when a newer run of the same pipeline passes.
- A flaky test is offered a re-run only, not a pull request that quarantines it.
- The Function App has no Application Insights, so its logs are not kept; the per-run trace table is the record.
- On run 78 the AI took about 9 minutes, against under a minute before; the run finished correctly.
