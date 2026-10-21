"""Every name PipeMedic depends on, in one place.

Each value can be overridden by an environment variable (the Function App's application settings use the same names),
so the same code can watch a different organization, project, pool or subscription without any edit.

Names of your own resources (subscription, Function App, storage, AI resource, organization) are not in the code. On a laptop they
come from agent/local.env (KEY=VALUE lines, never committed; infra/1_azure.sh writes it), in Azure from the app settings.
Values already in the environment win over the file.
"""
import os
from dataclasses import dataclass, field

LOCAL_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local.env")


def _load_local_env(path: str = LOCAL_ENV) -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_list(name: str, default: str) -> list[str]:
    return [p.strip().strip("/") for p in _env(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    # Azure DevOps
    org: str = field(default_factory=lambda: _env("ADO_ORG", ""))
    project: str = field(default_factory=lambda: _env("ADO_PROJECT", "pipemedic-pipes"))
    repo: str = field(default_factory=lambda: _env("ADO_REPO", "") or _env("ADO_PROJECT", "pipemedic-pipes"))
    pool: str = field(default_factory=lambda: _env("ADO_POOL", "pipemedic-pool"))
    branch: str = field(default_factory=lambda: _env("ADO_BRANCH", "main"))
    # Where code the AI may change lives, and what counts as a test or a pipeline definition
    code_roots: list[str] = field(default_factory=lambda: _env_list("CODE_ROOTS", "services"))
    test_markers: list[str] = field(default_factory=lambda: _env_list("TEST_MARKERS", "tests,test"))

    # Azure resources (used when running in Azure)
    subscription_id: str = field(default_factory=lambda: _env("AZURE_SUBSCRIPTION_ID", ""))
    resource_group: str = field(default_factory=lambda: _env("AZURE_RESOURCE_GROUP", "pipemedic-rg"))
    account_name: str = field(default_factory=lambda: _env("ACCOUNT_NAME", ""))
    health_api: str = field(default_factory=lambda: _env("HEALTH_API", ""))
    function_app: str = field(default_factory=lambda: _env("FUNCTION_APP", ""))
    state_table_url: str = field(default_factory=lambda: _env("STATE_TABLE_URL", ""))
    ai_endpoint: str = field(default_factory=lambda: _env("FOUNDRY_ENDPOINT", ""))
    ai_deployment: str = field(default_factory=lambda: _env("FOUNDRY_DEPLOYMENT", "gpt-5-mini"))
    ai_embed_deployment: str = field(default_factory=lambda: _env("FOUNDRY_EMBED_DEPLOYMENT", "embed-small"))

    # Notifications
    notify_from: str = field(default_factory=lambda: _env("NOTIFY_FROM", ""))
    notify_endpoint: str = field(default_factory=lambda: _env("NOTIFY_ENDPOINT", ""))

    @property
    def web_url(self) -> str:
        return f"https://dev.azure.com/{self.org}/{self.project}"

    def run_url(self, build_id: int) -> str:
        return f"{self.web_url}/_build/results?buildId={build_id}"

    def pr_url(self, pr_id: int) -> str:
        return f"{self.web_url}/_git/{self.repo}/pullrequest/{pr_id}"


def settings() -> Settings:
    """Read the settings now, so changes to the environment are picked up without restarting the process."""
    return Settings()


def is_test_path(path: str, markers: list[str] | None = None) -> bool:
    """True when a path looks like a test file: it sits in a test folder or is named like one."""
    markers = markers or settings().test_markers
    parts = [p.lower() for p in path.split("/")]
    name = parts[-1]
    if any(m in parts[:-1] for m in markers):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name.startswith("test.")


def under_code_root(path: str, roots: list[str] | None = None) -> bool:
    roots = roots or settings().code_roots
    return any(r == "" or path == r or path.startswith(r + "/") for r in roots)  # "" is the whole repository
