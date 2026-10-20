"""One place that decides how PipeMedic signs in to Azure.

In Azure (the Function App) it uses the app's managed identity. On a laptop it uses the Azure CLI, pinned to the
subscription that owns the resources so the right account is chosen even if another one is active.
"""
import os

import config

DEFAULT_SUBSCRIPTION = config.settings().subscription_id
DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"
ARM_SCOPE = "https://management.azure.com/.default"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def running_in_azure() -> bool:
    return bool(os.getenv("IDENTITY_ENDPOINT") or os.getenv("MSI_ENDPOINT"))


def get_credential():
    from azure.identity import AzureCliCredential, ChainedTokenCredential, DefaultAzureCredential, ManagedIdentityCredential

    if running_in_azure():
        return ManagedIdentityCredential()
    sub = os.getenv("FOUNDRY_SUBSCRIPTION") or DEFAULT_SUBSCRIPTION
    return ChainedTokenCredential(
        AzureCliCredential(subscription=sub) if sub else AzureCliCredential(),
        DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )
