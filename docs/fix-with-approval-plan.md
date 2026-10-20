# Plan: the agent fixes failures, after a person approves

## What a person sees

On a failed run's page, under "Why it failed", a **Fix it** section shows the agent's plan in plain language:
what it will do, what it will change (a diff for code and pipeline files; the exact action and machine for a runner),
how the result is checked, and how to undo it. The person presses **Approve** or **Reject**. The failure email carries the
same plan and an **Approve** link. After approval the page and an email show the progress: approved, running, done, verified.

## What the fix is

| Failure | The plan | After approval |
|---|---|---|
| Code: failing test, lint error, bad dependency pin, code error | An AI-written change, shown as a diff | A branch and a pull request, then a pipeline run on that branch to prove it. Never merged automatically. |
| Configuration in a pipeline file: a step memory limit, a timeout | A change to the pipeline YAML, shown as a diff and clearly marked "changes a pipeline file" | A pull request and a proving run, as above |
| Missing config file | The file to add, with placeholders for anything environment-specific | A pull request |
| Runner: disk full | Clean the agent's old job folders, caches and temp files on that VM | The cleanup script runs, then the run is re-queued |
| Runner: agent offline or stuck | Restart the agent service | Restart, then check the agent is online, then re-queue |
| Runner: CPU or memory taken by another process | Stop that named process (never the agent, ssh or system services) | Stop it, check health is normal again, then re-queue |
| Runner: still unhealthy, or nothing else works | Restart the VM | Restart through Azure, wait for the agent to be online and health to report, then re-queue |
| Flaky test | Re-run, or a pull request that quarantines the test | As chosen |
| Network, permissions | What a person must do outside our systems, and an optional re-run | Re-run only |

## Rules that keep it safe

- The model may write code and pipeline changes, but only into a pull request. Nothing is merged automatically.
- Runner actions come from a fixed set of scripts chosen by rules. The model never writes a command that runs on a machine.
- Every plan needs one explicit approval and expires after 24 hours, or sooner if a newer run or commit makes it stale.
- The email link opens a page that shows the plan and asks for a button press; opening the link alone approves nothing
  (mail scanners open links). The link is signed, expires, and works once. The page records who approved and how.
- Every action is logged with who approved it. The hourly limits on automatic actions stay.

## How it is built

1. Bring the Azure DevOps repo and the deployed agent in line with GitHub.
2. Demo branches in `pipemedic-pipes` with real, committed defects, so a pull request has a real bug to fix.
3. Fix plans: for each diagnosis, the agent works out the plan options and stores them with the diagnosis
   (`FixPlans` table): kind, steps, the diff or the action, the check afterwards, how to undo, expiry.
4. Approval: the dashboard's Fix it section, and a Function endpoint behind the signed email link with a confirmation page.
   Approving puts the plan on a queue.
5. Carrying it out: a queue-triggered function runs the plan with the Function App's identity: the pull request and its proving
   run, or the runner script through Azure run-command (or the VM restart), then the check and the re-run. Status and an email follow.
6. Live tests: `test-failure`, `lint-error` and `dependency-missing` end in a proven pull request; `memory-hog` in a pipeline-file
   pull request; `disk-full` is cleaned and re-run; `cpu-starvation` has the busy process stopped and re-runs; a stopped agent
   is restarted; and a VM restart brings a runner back.
