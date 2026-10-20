# PipeMedic accuracy benchmark

How well does the classifier name the cause of a pipeline failure on **real logs** it was not written around?

## Result

| Round | What was new | Failures scored | Original classifier | Classifier frozen before the round | AI model alone |
|---|---|---|---|---|---|
| 1 | Python tools (ruff, mypy, pytest). 36 used to fix rules, 13 held out | 13 held out | 9 (69%) | **13 (100%)** | 13 |
| 2 | New tools: flake8, black, pylint, jest, eslint, tsc, npm, docker | 23 | 9 (39%) | **17 (74%)** | 19 (83%) |
| 3 | New ecosystems: Go, Java/Maven, .NET, plus custom-message failures | 38 | 10 (26%) | **29 (76%)**, 31 (82%) with AI refinement | 36 (95%) |
| 4 | Fresh defects on the same tools, new failure styles | 27 | 9 (33%) | **27 (100%)** | 23 (85%) |

The classifier was **frozen before each round was built or run**. The fixes made after a round were tested on the next one, so
each frozen-version number is a first-sight result. Pooled over all 137 failures, the original classifier scores 42%; the
current one scores 100%, but that figure includes the failures it was fixed on and should not be quoted as an accuracy.

What to take from it:

- On tools and ecosystems the rules had not seen, expect **about 75%**. On tools they have seen, new defects score about 100%.
- The model alone is steadier across ecosystems (83 to 100%) and its quoted evidence was genuine 98% of the time, but it
  sometimes mislabels (a configuration problem as a code error, a refused connection as a dependency problem).
- The rules are precise, free and instant. The model covers what the rules do not know. The system uses both: the rules decide,
  and the AI may refine only the rules' generic "script failure" fallback when its quotes are in the log.

## What it measures

A real open-source Python project ([click](https://github.com/pallets/click), BSD-3) was imported into an Azure DevOps
repository, together with small Node, Go, Java/Maven, .NET and Docker projects. Ten ordinary CI pipelines run on
Microsoft-hosted agents: tests, lint, type check, package, style checks, web, container, Go, Java and .NET. On a clean `main`
all of them pass.

Each **defect** is one small commit on its own branch, off the same clean commit, of the kind a developer could make by
mistake: a changed condition, a mistyped pin, an unused import, a missing file, a test that needs the network. The pipeline
for that branch is run and the failing step's real log is collected, in the same shape the watcher collects it (failed
step, error issues, log text).

Nothing is simulated. The error text is whatever pytest, pip, ruff, mypy, flit, go, maven, dotnet, npm, docker, git or Azure
DevOps actually printed.

## Ground truth and fairness

- `catalog*.py` list every defect with its **cause label**, the name a person triaging it would give. Labels describe the
  cause, not the symptom: a missing `pip install` of pytest is `dependency` even though the failing step is the test step.
- The classifier never sees labels. `build_spec*.py` write `spec*.json` (what gets pushed: edits and a neutral commit message,
  no label) and `answers*.json` (the truth) separately. Branch names are neutral. A test in `agent/tests` fails if agent code
  ever mentions the benchmark or its answers.
- Round 1 held out every third defect per label before any log was read. Rules were fixed on the other two thirds.
- Cases a reasonable engineer could label two ways are `ambiguous` and left out of the headline number (six, listed by
  `compare.py`). Defects that did not fail are reported, not dropped (one: a package name that turned out to exist).
- Label policy: lint, format and type-check findings are `static-analysis`; syntax, import and compile errors are `code-error`;
  a wrong or missing configuration counts as `script-failure`; a custom, application-specific message ("other") is right if the
  classifier says unknown or a generic script failure and wrong if it names a specific cause.

## What the benchmark found

- A real bug: the permission rule could never match HTTP 403 or 401, because word boundaries had been saved as backspace
  characters. The hand-written samples never used those codes.
- `ImportError` was filed under dependency, so code errors (a bad import name, a circular import) looked like missing packages.
- The timeout rule fired when a step failed on its own and the job ran out of time later. It now requires the step to have been
  cancelled by the limit.
- Anchored rules (`^FAIL`) missed Azure DevOps lines because every line starts with a timestamp.
- Lint, format, type and compile failures had no category. Code error and static analysis were added.
- "Package or image not found" looks different in npm, apt, Docker, Maven, NuGet and Go. One rule now covers them.
- Lint and format tools often print only a file name. The classifier now reads the command the step ran.

## Files

| File | Purpose |
|---|---|
| `catalog.py`, `catalog2.py`, `catalog3.py`, `catalog4.py` | The defects of each round with their labels, and the baseline pipeline YAML |
| `build_spec*.py` | Write `spec*.json` (no labels) and `answers*.json` (labels) |
| `validate*.py` | Apply each defect to a local copy and check it really fails the tool it targets |
| `baseline_web/`, `baseline_r3/` | The clean Node, Go, Java and .NET projects the later rounds build on |
| `browser/push_and_queue.js`, `browser/collect_logs.js` | Run in the signed-in Azure DevOps tab: push the branches, queue the runs, collect the logs |
| `server.py` | Local bridge: receives collected logs from the browser (see below) |
| `score.py` | Runs the production classifier on each collected failure and compares with the answers |
| `compare.py` | Scores every saved classifier version on every round, in aggregate |
| `classifier_versions/` | The classifier as it was before each round (`v1_original`, `v2`, `v3`, `v4`) |
| `data/` | Truth, defect specs, the 144 real failing-step logs, and the model's cached answers |

## Run it

    python compare.py                                   # every classifier version on every round
    python score.py --split round3 --extended --misses  # one round, with the misses listed
    python score.py --split round3 --extended --ai      # also ask the model (needs `az login`)

`score.py` uses `../agent` unless `PIPEMEDIC_AGENT` points at another copy of the agent code.

To add a round: write `catalogN.py` with new defects, run `build_specN.py`, paste `data/specN.json` into the signed-in
Azure DevOps tab (`localStorage`), run `browser/push_and_queue.js`, wait, then run `browser/collect_logs.js` and `server.py`.
Freeze a copy of the classifier **before** building the round.

Moving logs out of the browser: the Azure DevOps pages block requests to localhost, but navigation is allowed. The collector
navigates to `http://127.0.0.1:8765/ingest.html#<gzip, base64>` and `server.py` saves what arrives. No tokens are used; the
page's own sign-in authorises the Azure DevOps calls.

## Limits

- One project plus small mini projects, and one author of the defects. They are realistic but not a random sample of real
  incidents. Real incidents will contain messages nobody here thought of.
- Round 4 tests fresh defects on tools the fixes were built from. It shows the fixes generalise across defects, not across new
  tool families (round 3 is the better guide for those: about 75%).
- Microsoft-hosted agents only: there is no runner-health signal, so the "runner problem" verdicts are not exercised.
- Flaky tests are not covered (they need two runs of one commit with different outcomes).
- A pipeline that fails before any step runs (for example a YAML error) has no step log and is not covered.
- Label calls are judgement. Six borderline cases were excluded rather than forced one way.
- The `ubuntu-latest` image moves over time (to Ubuntu 26 from 2026-10-19), so error text may shift on later runs. Do not pin
  `ubuntu-24.04`: on the free tier that label left jobs waiting for an agent that never arrived.
