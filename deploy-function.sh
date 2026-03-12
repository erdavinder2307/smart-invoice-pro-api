#!/bin/bash

# Azure Function Deployment Script for Smart Invoice Pro
# This script deploys the LowStockAlert function to Azure

set -e  # Exit on error

echo "🚀 Smart Invoice Pro - Azure Function Deployment"
echo "================================================"
echo ""

# Configuration
FUNCTION_APP_NAME="smartinvoice-inventory-alerts"
RESOURCE_GROUP="solidev"
FUNCTION_DIR="azure-functions"

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "❌ Azure CLI is not installed. Please install it:"
    echo "   brew install azure-cli"
    exit 1
fi

# Check if Functions Core Tools is installed
if ! command -v func &> /dev/null; then
    echo "❌ Azure Functions Core Tools not installed. Please install it:"
    echo "   brew install azure-functions-core-tools@4"
    exit 1
fi

# Check if logged in to Azure
echo "🔐 Checking Azure authentication..."
if ! az account show &> /dev/null; then
    echo "❌ Not logged in to Azure. Please run: az login"
    exit 1
fi

ACCOUNT_EMAIL=$(az account show --query user.name -o tsv)
echo "✅ Logged in as: $ACCOUNT_EMAIL"
echo ""

# Navigate to function directory
if [ ! -d "$FUNCTION_DIR" ]; then
    echo "❌ Function directory not found: $FUNCTION_DIR"
    exit 1
fi

cd "$FUNCTION_DIR"
echo "📁 Changed to directory: $(pwd)"
echo ""

# Check if Function App exists
echo "🔍 Checking if Function App exists..."
if ! az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    echo "❌ Function App '$FUNCTION_APP_NAME' not found in resource group '$RESOURCE_GROUP'"
    echo "   Please create it first using Azure Portal or Azure CLI"
    exit 1
fi

echo "✅ Function App found: $FUNCTION_APP_NAME"
echo ""

# Check Function App state
STATE=$(az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --query state -o tsv)
if [ "$STATE" != "Running" ]; then
    echo "⚠️  Function App is in state: $STATE"
    echo "   Starting Function App..."
    az functionapp start --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP"
fi

# Show current app settings (without values)
echo "📋 Current Function App Settings:"
az functionapp config appsettings list \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "[].name" -o tsv | sort
echo ""

# Ask for confirmation
read -p "📤 Deploy to Function App '$FUNCTION_APP_NAME'? (y/N): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "❌ Deployment cancelled"
    exit 0
fi

echo ""
echo "🚀 Starting deployment..."
echo "================================================"
echo ""

# Deploy function
func azure functionapp publish "$FUNCTION_APP_NAME"

echo ""
echo "================================================"
echo "✅ Deployment Complete!"
echo ""
echo "📊 Next Steps:"
echo "   1. Verify deployment in Azure Portal:"
echo "      https://portal.azure.com/#resource/subscriptions/8dfb8ce9-340f-4cfc-aa92-89d6d46d0924/resourceGroups/solidev/providers/Microsoft.Web/sites/$FUNCTION_APP_NAME"
echo ""
echo "   2. Check function execution logs:"
echo "      az functionapp log tail --name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP"
echo ""
echo "   3. Monitor in Azure Portal:"
echo "      Function App > Functions > LowStockAlert > Monitor"
echo ""
echo "   4. Next scheduled execution: 9:00 AM UTC (Daily)"
echo ""
echo "   5. Test manually (optional):"
echo "      - Navigate to Function in Azure Portal"
echo "      - Click 'Test/Run'"
echo "      - Or trigger backend endpoint:"
echo "        curl -X POST 'https://smartinvoicepro.azurewebsites.net/api/cron/check-low-stock?send_email=true'"
echo ""
echo "================================================"
