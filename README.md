# PipeMedic

An Azure DevOps **agent pool health monitor and pipeline failure investigator**.

Azure DevOps tells you that a pipeline failed. It does not tell you whether the code is broken or the self-hosted runner was sick.
PipeMedic reads the failed run's logs, finds the runner that ran it, and joins them with that runner's CPU, memory and disk readings
and its own system logs from the same minutes. It then says what failed, why, whether the runner was to blame, and what to do,
by email within a minute of the failure and on a dashboard. It also watches the agent pool itself.

## How it works

1. Each runner VM reports its health every minute to an Azure Function, which stores it in Table Storage.
2. When a run fails, an Azure DevOps service hook calls the Function with the run's id. The Function queues it and investigates at once
   (a one-minute timer catches anything the hook missed).
3. The agent reads every failed step's log, the runner's health for the run's own time window, and the runner's system, kernel and
   agent logs. Rules decide the verdict: **code or configuration**, **the runner**, **both**, or **a flaky test**. A model in Azure AI
   Foundry adds a labelled second opinion; its quotes must appear in the real logs.
4. The person who triggered the run gets an email with the cause, the evidence and what to do. Pool problems (an agent offline,
   a stuck queue, a disk heading for full) are emailed too.

`docs/workflow-map.html` draws all of this with the Azure services involved; open it in a browser.

## What is in this repository

| Folder | What it is |
|---|---|
| `agent/` | The investigator, the pool monitor, the email, the AI second opinion and fixes, and the dashboard (`streamlit run dashboard.py`) |
| `health-api/` | The Azure Function: receives runner health, serves its history, takes the service hook, runs the investigation |
| `runner-health/` | The health report every runner runs each minute, its systemd units, and the script that turns a fresh VM into a runner |
| `pipemedic-pipes/` | Four small services and realistic pipelines (lint, test, benchmark, package, deploy, smoke test) with ten failure scenarios to demonstrate PipeMedic on |
| `infra/` | Scripts that create everything in a new Azure subscription and Azure DevOps organization |
| `scripts/` | Creating the Azure DevOps service hooks |
| `benchmark/` | How accurately the rules name the cause, measured on 137 real pipeline failures |

## Set it up

See **[SETUP.md](SETUP.md)**: four steps (Azure resources, deploy, Azure DevOps, runners), each with a dry run first.

## Try it

Run any pipeline in `pipemedic-pipes` with a `scenario` (for example `disk-full` or `test-failure`; the list is in
`pipemedic-pipes/README.md`). Within a minute an email explains the failure, and the dashboard shows it:

```bash
cd agent
pip install -r requirements.txt
streamlit run dashboard.py        # pick organization, project and repository; click a pipeline, then a run
```

## How it stays safe

- **Rules decide, AI explains.** The verdict and every action come from rules; the model's opinion is labelled and cannot change them,
  except to refine a generic script failure when its quoted evidence is found in the log.
- **No keys in the cloud.** The Function App, the runners and the dashboard sign in with managed identities or your `az login`.
  The only secret, the key runners post health with, is in Key Vault.
- **Secrets are removed from logs** before anything is sent to the model.
- **Guardrailed actions.** On its own the agent may only retry a run once, clean a full runner disk, or open a work item, and only
  when switched on. AI-written fixes become pull requests that a person reviews; nothing is merged automatically.

## Tests

```bash
cd agent && python -m pytest tests          # 265 tests
python benchmark/compare.py                 # classifier accuracy on the collected failures
```
