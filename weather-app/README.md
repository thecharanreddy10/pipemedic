# Weather app

A small FastAPI service with a page and a JSON API for the current weather in a city, built as a container image on the
self-hosted runners, pushed to Azure Container Registry, and deployed to Azure Container Apps. It is a test bed for PipeMedic:
its pipeline fails in the ways container pipelines really fail, and PipeMedic explains each failure and offers a fix.

| Path | What it is |
|---|---|
| `app/` | The service. `GET /`, `GET /api/weather?city=…`, `GET /health`. Data from Open-Meteo (no API key); `WEATHER_MODE=mock` uses built-in sample cities |
| `tests/` | Unit tests for the conversions, the Open-Meteo parsing and the API |
| `Dockerfile` | `python:3.12-slim`, runs as a non-root user on port 8000 |
| `pipelines/weather-app.yml` | Lint and test → build the image with Docker on the runner → push to ACR → deploy a new revision to Container Apps → wait until it is healthy → smoke test |
| `pipelines/scenarios/` | Runner failure scenarios and the clean-up that always runs |
| `pipelines/scripts/` | Waiting for the revision (and printing its logs when it fails), and the smoke test |

The runner VMs sign in to Azure with their managed identity: **AcrPush** on the registry and **Contributor** on the container app.
The container app pulls from the registry with its own identity (**AcrPull**). The pipeline holds no secrets.

## Failure scenarios

Runner problems are chosen with the pipeline's `scenario` parameter. Defects in the code, the Dockerfile or the pipeline are real
commits on `demo/*` branches: run the pipeline on the branch with no scenario.

| Scenario | How to start it | Fails at | What PipeMedic concluded (run) | Fix it offered |
|---|---|---|---|---|
| Disk full during the image build | scenario `docker-disk-full` | Build the image | The runner ran out of disk (95) | Clean the disk, then re-run |
| Docker stopped on the runner | scenario `docker-daemon-down` | Build the image | Docker is not running on the runner (86) | Start Docker, then re-run. Approved: Docker started and re-run 101 passed |
| Registry permission removed | remove the runners' AcrPush role by hand | Sign in to the registry | The registry exists but the runner's identity has no role on it (106) | A job for a person: PipeMedic does not change access |
| Wrong temperature conversion | branch `demo/test-failure` | Unit tests | Test failure in the code (96) | Pull request changing `celsius * 5 / 9` back to `celsius * 9 / 5` |
| Base image tag that does not exist | branch `demo/bad-base-image` | Build the image | The base image cannot be found (107) | Pull request back to `python:3.12-slim` |
| Dependency pinned to a release that does not exist | branch `demo/dependency-missing` | Install dependencies | Missing package version (108) | Pull request back to `httpx==0.27.2` |
| Wrong registry name in the pipeline | branch `demo/wrong-registry` | Sign in to the registry | No registry by that name exists (109) | Pull request to the pipeline file fixing `ACR_NAME` |
| App listens on 8080, Container Apps sends traffic to 8000 | branch `demo/wrong-port` | Wait for the revision | The new revision did not start; its start-up probe failed (98) | Pull request back to port 8000. Approved: PR 9, proving run 100 passed |
| Required setting missing, so the app stops at start-up | branch `demo/missing-setting` | Wait for the revision | The new revision did not start (103) | Pull request restoring `WEATHER_MODE=live` |

PipeMedic tells "wrong name" and "no access" apart by checking whether the named resource exists. Code fixes are written with the
same files from the pipeline's last passing run in view, so they restore what worked rather than invent a new value.

After a demo, abandon the fix pull request instead of merging it, so the branch keeps its defect.

## Run it locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && python -m pytest
WEATHER_MODE=mock uvicorn app.main:app --port 8000
```
