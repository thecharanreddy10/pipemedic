#!/usr/bin/env bash
# Step 1: creates the Azure resources PipeMedic uses and connects them (identity, roles, settings).
# Safe to run again: an existing resource is left as it is.
#
#   bash infra/1_azure.sh            # needs az login and infra/setup.env
#   DRY_RUN=1 bash infra/1_azure.sh  # prints the commands, changes nothing
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/setup.env"
DRY="${DRY_RUN:-0}"

run() { if [ "$DRY" = "1" ]; then echo "+ $*"; else "$@"; fi; }
ask() { if [ "$DRY" = "1" ]; then echo "<${*: -1}>"; else az "$@"; fi; }   # a query whose answer later steps need
exists() { [ "$DRY" != "1" ] && az "$@" -o none 2>/dev/null; }
role() {  # role <principal id> <role name> <scope>
  if [ "$DRY" = "1" ]; then echo "+ az role assignment create --role '$2' (principal $1) --scope $3"; return; fi
  if [ -z "$(az role assignment list --assignee "$1" --role "$2" --scope "$3" --query '[0].id' -o tsv)" ]; then
    az role assignment create --assignee-object-id "$1" --assignee-principal-type ServicePrincipal --role "$2" --scope "$3" -o none
  fi
  echo "  role: $2"
}

run az account set --subscription "$SUBSCRIPTION"
run az extension add --name communication --upgrade --yes --only-show-errors

echo "== resource group, storage, Key Vault"
run az group create -n "$RG" -l "$LOCATION" -o none
exists storage account show -n "$STORAGE" -g "$RG" || run az storage account create -n "$STORAGE" -g "$RG" -l "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false -o none
exists keyvault show -n "$KEY_VAULT" -g "$RG" || run az keyvault create -n "$KEY_VAULT" -g "$RG" -l "$LOCATION" --enable-rbac-authorization true -o none

echo "== Function App (Linux, Consumption plan, Python 3.11) with its own identity"
exists functionapp show -n "$FUNCTION_APP" -g "$RG" || run az functionapp create -n "$FUNCTION_APP" -g "$RG" \
  --consumption-plan-location "$LOCATION" --os-type Linux --runtime python --runtime-version 3.11 --functions-version 4 \
  --storage-account "$STORAGE" -o none
run az functionapp identity assign -n "$FUNCTION_APP" -g "$RG" -o none
FUNC_ID=$(ask functionapp identity show -n "$FUNCTION_APP" -g "$RG" --query principalId -o tsv)

echo "== AI Services: the model and the embeddings, Entra sign-in only (no keys)"
exists cognitiveservices account show -n "$AI_ACCOUNT" -g "$RG" || run az cognitiveservices account create -n "$AI_ACCOUNT" -g "$RG" \
  -l "$AI_LOCATION" --kind AIServices --sku S0 --custom-domain "$AI_ACCOUNT" --yes -o none
AI_RID=$(ask cognitiveservices account show -n "$AI_ACCOUNT" -g "$RG" --query id -o tsv)
run az resource update --ids "$AI_RID" --set properties.disableLocalAuth=true -o none
exists cognitiveservices account deployment show -g "$RG" -n "$AI_ACCOUNT" --deployment-name gpt-5-mini || run az cognitiveservices account deployment create \
  -g "$RG" -n "$AI_ACCOUNT" --deployment-name gpt-5-mini --model-name gpt-5-mini --model-version 2025-08-07 --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 50 -o none
exists cognitiveservices account deployment show -g "$RG" -n "$AI_ACCOUNT" --deployment-name embed-small || run az cognitiveservices account deployment create \
  -g "$RG" -n "$AI_ACCOUNT" --deployment-name embed-small --model-name text-embedding-3-small --model-version 1 --model-format OpenAI \
  --sku-name GlobalStandard --sku-capacity 20 -o none

echo "== Email: Communication Services with an Azure-managed sender domain"
exists communication email show -n "$EMAIL_SERVICE" -g "$RG" || run az communication email create -n "$EMAIL_SERVICE" -g "$RG" \
  --location global --data-location "$EMAIL_DATA_LOCATION" -o none
exists communication email domain show --domain-name AzureManagedDomain --email-service-name "$EMAIL_SERVICE" -g "$RG" || run az communication email domain create \
  --domain-name AzureManagedDomain --email-service-name "$EMAIL_SERVICE" -g "$RG" --location global --domain-management AzureManaged -o none
DOMAIN_ID=$(ask communication email domain show --domain-name AzureManagedDomain --email-service-name "$EMAIL_SERVICE" -g "$RG" --query id -o tsv)
MAIL_DOMAIN=$(ask communication email domain show --domain-name AzureManagedDomain --email-service-name "$EMAIL_SERVICE" -g "$RG" --query mailFromSenderDomain -o tsv)
exists communication show -n "$COMM_SERVICE" -g "$RG" || run az communication create -n "$COMM_SERVICE" -g "$RG" --location global \
  --data-location "$EMAIL_DATA_LOCATION" --linked-domains "$DOMAIN_ID" -o none
COMM_HOST=$(ask communication show -n "$COMM_SERVICE" -g "$RG" --query hostName -o tsv)

echo "== roles for the Function App's identity"
SUB_ID=$(ask account show --query id -o tsv)
role "$FUNC_ID" "Storage Table Data Contributor" "$(ask storage account show -n "$STORAGE" -g "$RG" --query id -o tsv)"
role "$FUNC_ID" "Key Vault Secrets User" "$(ask keyvault show -n "$KEY_VAULT" -g "$RG" --query id -o tsv)"
role "$FUNC_ID" "Cognitive Services OpenAI User" "$AI_RID"
role "$FUNC_ID" "Communication and Email Service Owner" "$(ask communication show -n "$COMM_SERVICE" -g "$RG" --query id -o tsv)"
role "$FUNC_ID" "Virtual Machine Contributor" "/subscriptions/$SUB_ID/resourceGroups/$RG"   # reads the runners' logs
role "$FUNC_ID" "Reader" "/subscriptions/$SUB_ID/resourceGroups/$RG"                         # checks whether a resource a failed run names exists

echo "== your own access (so the dashboard and the next steps work with your az login)"
ME=$(ask ad signed-in-user show --query id -o tsv)
for r in "Storage Table Data Contributor:$(ask storage account show -n "$STORAGE" -g "$RG" --query id -o tsv)" \
         "Key Vault Secrets Officer:$(ask keyvault show -n "$KEY_VAULT" -g "$RG" --query id -o tsv)" \
         "Cognitive Services OpenAI User:$AI_RID"; do
  if [ "$DRY" = "1" ]; then echo "+ az role assignment create --role '${r%%:*}' (you)"; continue; fi
  [ -n "$(az role assignment list --assignee "$ME" --role "${r%%:*}" --scope "${r#*:}" --query '[0].id' -o tsv)" ] || \
    az role assignment create --assignee-object-id "$ME" --assignee-principal-type User --role "${r%%:*}" --scope "${r#*:}" -o none
done

echo "== Function App settings"
# Signs the approval links in emails. Kept when it already exists, so links already sent keep working.
LINK_SECRET=$(az functionapp config appsettings list -n "$FUNCTION_APP" -g "$RG" --query "[?name=='FIX_LINK_SECRET'].value | [0]" -o tsv 2>/dev/null || true)
[ -n "$LINK_SECRET" ] || LINK_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
run az functionapp config appsettings set -n "$FUNCTION_APP" -g "$RG" -o none --settings \
  ADO_ORG="$ADO_ORG" ADO_PROJECT="$ADO_PROJECT" ADO_REPO="$ADO_PROJECT" ADO_POOL="$ADO_POOL" \
  AZURE_SUBSCRIPTION_ID="$SUB_ID" AZURE_RESOURCE_GROUP="$RG" \
  HEALTH_API="https://$FUNCTION_APP.azurewebsites.net" STATE_TABLE_URL="https://$STORAGE.table.core.windows.net" \
  FOUNDRY_ENDPOINT="https://$AI_ACCOUNT.cognitiveservices.azure.com" FOUNDRY_DEPLOYMENT=gpt-5-mini FOUNDRY_EMBED_DEPLOYMENT=embed-small \
  AI_IN_WATCHER=1 MEMORY=1 FIX_MODE="$FIX_MODE" ACT_ENABLED="$ACT_ENABLED" \
  FIX_PLANS=1 FIX_LINK_SECRET="$LINK_SECRET" \
  NOTIFY_ENABLED=1 NOTIFY_ENDPOINT="https://$COMM_HOST" NOTIFY_FROM="DoNotReply@$MAIL_DOMAIN" NOTIFY_DEFAULT_TO="$NOTIFY_TO" \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true

echo "== agent/local.env (your resource names for the dashboard and the tools on this machine; not committed)"
LOCAL_ENV="$HERE/../agent/local.env"
if [ "$DRY" = "1" ]; then echo "+ write $LOCAL_ENV"; else cat > "$LOCAL_ENV" <<LOCAL
# Written by infra/1_azure.sh. Read by agent/config.py on this machine; never committed.
ADO_ORG=$ADO_ORG
ADO_PROJECT=$ADO_PROJECT
ADO_POOL=$ADO_POOL
AZURE_SUBSCRIPTION_ID=$SUB_ID
AZURE_SUBSCRIPTION=$SUB_ID
AZURE_RESOURCE_GROUP=$RG
FUNCTION_APP=$FUNCTION_APP
HEALTH_API=https://$FUNCTION_APP.azurewebsites.net
STATE_TABLE_URL=https://$STORAGE.table.core.windows.net
FOUNDRY_ENDPOINT=https://$AI_ACCOUNT.cognitiveservices.azure.com
LOCAL
fi

cat <<DONE

Azure resources are ready. Next:
  python health-api/deploy.py --store-key-in $KEY_VAULT
DONE
