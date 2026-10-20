# pipemedic-pipes

Four small services and realistic pipelines for them. They run on the self-hosted `pipemedic-pool` runners and give
[PipeMedic](https://dev.azure.com/Hackathon925/Pipemedic/_git/Pipemedic) real failures to investigate.

## What is here

| Folder | What it is |
|---|---|
| `services/` | The application code: `frontend`, `orders`, `payments`, `inventory` (FastAPI), each with tests, a lint setup and a deployment config |
| `pipelines/` | One pipeline per service, a shared template, the failure scenarios, and the scripts the pipelines call |

## The pipelines

`frontend-ci`, `orders-ci`, `payments-ci` and `inventory-ci` all follow `pipelines/templates/service-ci.yml`:

1. **Lint and test**: check network access, install dependencies, lint (ruff), unit tests (published as test results), benchmark.
2. **Package**: validate the deployment config, build the package.
3. **Deploy to the runner**: install the service as a systemd service on the runner that picked up the job (frontend 8001, payments 8002,
   inventory 8003, orders 8004), then a smoke test against `/health`.

Every pipeline triggers on changes to its own service or to `pipelines/`, and validates pull requests (the deploy stage is skipped for those).

Runners need Python 3 with `venv`, `systemd`, `curl`, `fallocate`, and passwordless `sudo` for the agent user.

## Failure scenarios

`main` is green. Run a pipeline by hand and pick a **scenario** (the `scenario` run parameter) to produce a failure. Each one fails inside the step
where that failure would really happen, so the failing step has its normal name.

| Scenario | What happens | Failing step |
|---|---|---|
| `none` | nothing: a normal run | |
| `test-failure` | a defect is applied to the service code | Unit tests |
| `lint-error` | an unused import is added | Lint |
| `dependency-missing` | httpx is pinned to a release that does not exist | Install dependencies |
| `missing-config` | the deployment config file is removed | Validate deployment config |
| `network-timeout` | the package index address does not answer | Check network access |
| `permission-denied` | the deploy step runs without elevated rights | Deploy to this runner |
| `flaky-test` | a test that depends on the build number (fails on odd ids) | Unit tests |
| `memory-hog` | the benchmark needs 1500 MB but its step is limited to 600 MB, so the kernel kills it (the machine itself has memory to spare: this is a configuration problem, not the runner's) | Benchmark |
| `cpu-starvation` | other work uses every CPU on the runner, and the full-size benchmark is run | Benchmark |
| `disk-full` | the runner disk is filled, 60 MB left | Install dependencies |

The runner scenarios are real: the busy CPU and the full disk happen on the VM, so the health readings and the runner's own logs show them.
The memory kill is real too, but it is the step's own limit, so the runner's memory stays low and the kernel log says `Memory cgroup out of memory`.
Each one cleans up after itself in an always-run step, and a timer removes the disk fill even if the agent dies.

How it works: each step starts with `source pipelines/scenarios/hooks.sh <phase> <service>`. With no scenario it does nothing. With one, it changes the
workspace or the environment just before the step runs, and the step then fails the way it would for real.

## Run it locally

```bash
cd services/inventory
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && python -m pytest
```
