# Setting PipeMedic up in a new subscription

This rebuilds everything: the Azure resources, the Azure DevOps project with the demo pipelines, and the runner VMs.
It takes about 45 minutes, most of it waiting for Azure. Each script is safe to run again, and each can be run with
a dry run first (`DRY_RUN=1` for the shell scripts, `--dry-run` for the Python one) to see every command without changing anything.

## Before you start

- An Azure subscription where you can create resources and **assign roles** (Owner, or Contributor plus User Access Administrator).
- In that subscription's region of choice, quota for **gpt-5-mini** and **text-embedding-3-small** (GlobalStandard). Not every region
  offers them; set `AI_LOCATION` to one that does.
- An **Azure DevOps organization** where you are a Project Collection Administrator, in the same Microsoft Entra tenant as the subscription.
- A new organization has **no free parallel job for self-hosted agents in private projects** until it is granted. Request it once at
  https://aka.ms/azpipelines-parallelism-request, or the pipelines wait in the queue.
- An Azure DevOps **access token with "Agent Pools: Read & manage"**, only for registering the runners. Make it short-lived.
- On your machine: Azure CLI 2.60 or newer (`az login` done), Python 3.11 or newer, Git, and Bash (Git Bash on Windows works).

```bash
cd agent && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && cd ..   # used by steps 2 and 4
cp infra/setup.env.example infra/setup.env    # then edit it: names, region, organization, email address
```

## The steps

| Step | Command | What it does |
|---|---|---|
| 1. Azure | `bash infra/1_azure.sh` | Resource group, storage account, Key Vault, Function App (Linux Consumption, Python 3.11) with its own identity, AI Services with the two models (keys disabled), email (Communication Services with an Azure-managed sender), the identity's five roles, your own roles, and every app setting |
| 2. Code | `python health-api/deploy.py --app <FUNCTION_APP> --group <RG> --subscription <SUBSCRIPTION> --store-key-in <KEY_VAULT>` | Packages and deploys the agent, waits until the new version answers, and saves the Function key in Key Vault for the runners |
| 3. Azure DevOps | `python infra/2_devops.py` | Agent pool, the project and its repository filled from `pipemedic-pipes/`, the four pipelines and their permission to use the pool, the Function App's identity as a project Contributor, and the two service hooks |
| 4. Runners | `export AZDO_PAT=<token>` then `bash infra/3_runners.sh` | The runner VMs (Ubuntu 24.04), each with Key Vault access, the health report every minute, the Azure DevOps agent as a service, passwordless sudo for the pipelines, and an optional daily shutdown |

## Check it works

1. `https://<FUNCTION_APP>.azurewebsites.net/api/version` answers with a version.
2. `https://<FUNCTION_APP>.azurewebsites.net/api/health-history?minutes=5` lists readings from every runner.
3. In Azure DevOps, the pool shows every agent **online**.
4. Run `inventory-ci` with the scenario `none`: it passes and deploys the service to a runner.
5. Run it again with the scenario `disk-full`: within a minute of the failure an email arrives saying the runner ran out of disk space.
6. The dashboard on your laptop: set the variables `1_azure.sh` prints at the end, then `cd agent && streamlit run dashboard.py`.

## What it costs and how to stop it

The runner VMs and the model calls cost money; the rest is small. Stop the VMs when not in use (`az vm deallocate -g <RG> -n <VM>`),
or keep `AUTO_SHUTDOWN_TIME` set. To remove everything: `az group delete -n <RG>` and delete the Azure DevOps project and pool.

## Limits

- The scripts were checked with dry runs against the current setup; they have not been run end to end in an empty subscription yet.
- PipeMedic watches one Azure DevOps project (`ADO_PROJECT`).
- `FIX_MODE=open` lets the agent open pull requests with AI-written fixes (never merged automatically); `propose` only describes them.
