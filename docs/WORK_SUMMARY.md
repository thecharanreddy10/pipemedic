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

317 automated tests pass. On 137 real failures from an open-source project, the rules name the cause correctly in 137 and
the rules with the AI's refinement in 135.

## Container test bed: the weather app

A second repository in the same project, `weather-app`: a FastAPI service with a page and an API (Open-Meteo data), built as an
image **with Docker on the self-hosted runners**, pushed to **Azure Container Registry**, and deployed as a new revision of an
**Azure Container Apps** app, then checked: the revision must become healthy and answer a smoke test. The runners sign in with their
managed identities (AcrPush on the registry, Contributor on the app, Reader on the environment and its log workspace), so the
pipeline holds no secrets. `infra/4_weather_app.sh` creates it in a new subscription.

What PipeMedic gained for it:

| Change | Why |
|---|---|
| A fix goes to the repository the failed run built, and the files it may change are worked out from that repository | The weather app keeps its code at the root (`app/`, `Dockerfile`), not under `services/` |
| Rules for Docker not running, a missing base image, registry refusals, read and TLS timeouts, an Azure resource not found, and a Container Apps revision that does not start | Container pipelines fail in these ways |
| "Resource not found" is checked against Azure with PipeMedic's own read access | Azure gives the same answer for a wrong name and for an identity with no role on the resource |
| The model sees each file as it was in the pipeline's last passing run, when it differs | Without it, the model guessed values (`python:3.9`, `WEATHER_MODE=mock`, port 8080) instead of restoring what worked |
| An edit to a file the model was not shown, or one that throws away most of a file, is refused; a small app is shown whole | On one run the model, not shown `app/weather.py`, rewrote it from scratch; the checks had let that through |
| A runner action that restarts Docker, and Docker clean-up in the disk clean-up | For the Docker-down and full-disk failures |
| The deploy check prints the port Container Apps uses and the errors a crashed container left in Log Analytics, and fails at once on `ActivationFailed` | A crashed container leaves no replica to read logs from, and the port is set outside the repository |

Problems found while building it:

| Problem | What it caused | Fix |
|---|---|---|
| Azure's network drops full-size packets from Docker's bridge network | Every `pip install` inside the image build timed out (run 79) | Docker on the runners uses a 1400-byte MTU; added to the runner bootstrap |
| `az ... -o tsv` prints each field of a list on its own line | The deploy check never saw the revision as healthy (run 80) | Read the revision's state as JSON with `jq` |
| The test-failure rule matched pytest's `FAILED ` marker without regard to case | Docker's "failed to resolve reference" was called a failed test (run 87) | The pytest and Go markers are matched in upper case only; the benchmark still scores 137 of 137 |
| A fix waiting on one branch blocked fixes for the same step on other branches | The missing-setting branch got no fix while the wrong-port fix was open (run 99) | Waiting fixes are compared on the same branch only |
| The email service limits how many messages a domain sends a minute | A failure email was not sent during heavy testing (run 98) | Up to two short retries when the service says it is busy |

All nine scenarios were run live and each was diagnosed correctly with the right fix offered; two were approved and carried out:

| Run | Scenario | What PipeMedic concluded and offered |
|---|---|---|
| 95 | Disk full during the image build | The runner ran out of disk; clean the disk, then re-run |
| 86 | Docker stopped on the runner | Docker is not running; start it, then re-run. **Approved:** Docker started and re-run 101 passed |
| 106 | The runners' push role removed | The registry exists but the runner's identity has no role on it; a job for a person |
| 96 | Wrong temperature formula | Pull request changing `celsius * 5 / 9` back to `celsius * 9 / 5` |
| 107 | Base image tag that does not exist | Pull request back to `python:3.12-slim` |
| 108 | Dependency pinned to a release that does not exist | Pull request back to `httpx==0.27.2` |
| 109 | Wrong registry name in the pipeline | No registry by that name exists; pull request to the pipeline file |
| 98 | App listens on 8080, Container Apps sends traffic to 8000 | Pull request back to port 8000. **Approved:** pull request 9 in `weather-app`, proving run 100 passed |
| 103 | A required setting removed, so the app stops at start-up | Pull request restoring `WEATHER_MODE=live` |

## Not done yet, and known limits

- Restarting the agent and restarting a VM have tests but have not been run on a live runner.
- Whether the result emails for runs 68 to 70 reached the inbox was not confirmed; from now on each plan records its email status.
- A plan expires after 24 hours but is not withdrawn when a newer run of the same pipeline passes.
- A flaky test is offered a re-run only, not a pull request that quarantines it.
- The Function App has no Application Insights, so its logs are not kept; the per-run trace table is the record.
- On run 78 the AI took about 9 minutes, against under a minute before; the run finished correctly.
- The email service's sending limit can delay or drop emails when many runs fail at once; the dashboard still shows every diagnosis.
