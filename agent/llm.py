"""Client for the model deployed in Azure AI Foundry.

Signs in with Microsoft Entra ID (your `az login`), so there is no API key to store or leak. The signed-in
account needs the "Cognitive Services OpenAI User" role on the Foundry resource.

Environment (defaults come from config.py):
  FOUNDRY_ENDPOINT    the Azure AI Foundry endpoint, e.g. https://<resource>.cognitiveservices.azure.com
  FOUNDRY_DEPLOYMENT  deployment name, default gpt-5-mini
  FOUNDRY_SUBSCRIPTION  subscription that owns the resource; the right Azure account is chosen from it
  AI_EFFORT           reasoning effort: minimal, low, medium or high (default low)
  AI_DISABLED=1       turn every AI feature off
"""
import os
import time

import requests

import config

DEFAULT_ENDPOINT = config.settings().ai_endpoint
DEFAULT_DEPLOYMENT = config.settings().ai_deployment
DEFAULT_EMBED_DEPLOYMENT = config.settings().ai_embed_deployment
DEFAULT_SUBSCRIPTION = config.settings().subscription_id
DEFAULT_ACCOUNT = config.settings().account_name
SCOPE = "https://cognitiveservices.azure.com/.default"


class AIUnavailable(RuntimeError):
    """The model could not be reached. The message is safe to show to a user."""


class FoundryLLM:
    def __init__(self, endpoint=None, deployment=None, effort=None):
        self.endpoint = (endpoint or os.getenv("FOUNDRY_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
        self.deployment = deployment or os.getenv("FOUNDRY_DEPLOYMENT") or DEFAULT_DEPLOYMENT
        self.effort = effort or os.getenv("AI_EFFORT") or "low"
        self.subscription = os.getenv("FOUNDRY_SUBSCRIPTION") or DEFAULT_SUBSCRIPTION
        self.label = f"{self.deployment} via Azure AI Foundry"
        self._credential = None

    def _token(self) -> str:
        try:
            from azure_auth import COGNITIVE_SCOPE, get_credential
        except ImportError as e:
            raise AIUnavailable("Install azure-identity (pip install azure-identity).") from e
        try:
            if self._credential is None:
                self._credential = get_credential()
            return self._credential.get_token(COGNITIVE_SCOPE).token
        except Exception as e:
            raise AIUnavailable(
                "Not signed in to the Azure account that owns the AI resource. Run `az login` "
                f"{'with ' + DEFAULT_ACCOUNT + ' ' if DEFAULT_ACCOUNT else ''}and try again.") from e

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_schema: dict | None = None,
             max_tokens: int = 4000) -> dict:
        """One model call. Returns the assistant message (may contain tool_calls)."""
        body = {"model": self.deployment, "messages": messages, "max_completion_tokens": max_tokens,
                "reasoning_effort": self.effort}
        if tools:
            body["tools"] = tools
        if json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": json_schema, "strict": True}}
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                r = requests.post(f"{self.endpoint}/openai/v1/chat/completions", headers=headers, json=body, timeout=120)
            except requests.RequestException as e:
                raise AIUnavailable(f"Could not reach the model endpoint: {e.__class__.__name__}") from e
            if r.status_code == 429 and attempt < 2:  # rate limited: wait and try again
                time.sleep(min(int(r.headers.get("Retry-After", "3")), 20))
                continue
            break
        if "does not match resource tenant" in r.text:
            raise AIUnavailable(
                "You are signed in to a different Azure tenant than the AI resource. Run `az login`"
                f"{' with ' + DEFAULT_ACCOUNT if DEFAULT_ACCOUNT else ''}"
                f"{', or `az account set --subscription ' + self.subscription + '`' if self.subscription else ''}, then try again.")
        if r.status_code in (401, 403):
            raise AIUnavailable("Access denied. Your account needs the 'Cognitive Services OpenAI User' role on the Foundry resource.")
        if not r.ok:
            raise AIUnavailable(f"The model returned {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]


def _embed(self, texts: list[str], dimensions: int = 256) -> list[list[float]]:
    """Embed short texts with the embedding deployment. Returns one vector per text."""
    deployment = os.getenv("FOUNDRY_EMBED_DEPLOYMENT") or DEFAULT_EMBED_DEPLOYMENT
    headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
    try:
        r = requests.post(f"{self.endpoint}/openai/v1/embeddings", headers=headers, timeout=60,
                          json={"model": deployment, "input": texts, "dimensions": dimensions})
    except requests.RequestException as e:
        raise AIUnavailable(f"Could not reach the embedding endpoint: {e.__class__.__name__}") from e
    if not r.ok:
        raise AIUnavailable(f"The embedding model returned {r.status_code}: {r.text[:200]}")
    return [row["embedding"] for row in sorted(r.json()["data"], key=lambda row: row["index"])]


FoundryLLM.embed = _embed


def ai_enabled() -> bool:
    return os.getenv("AI_DISABLED", "") not in ("1", "true", "yes")


def get_llm():
    """The configured model client, or None when AI is turned off."""
    return FoundryLLM() if ai_enabled() else None
