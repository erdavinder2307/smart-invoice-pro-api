# Azure Deployment Guide - Smart Invoice Pro

This guide provides step-by-step instructions for deploying and configuring the Smart Invoice Pro inventory management system with Azure services.

## Azure Resources Overview

### Currently Configured Resources

| Resource | Name | Type | Location | Purpose |
|----------|------|------|----------|---------|
| Web App | `smartinvoicepro` | App Service | Central India | Frontend & Backend API |
| Function App | `smartinvoice-inventory-alerts` | Azure Functions | East US | Automated inventory alerts |
| Email Service | `solidev-email-send-resource-3` | Communication Service | Global | Email notifications |
| Email Domain | `solidevelectrosoft.com` | Verified Domain | Global | Sender verification |
| Storage Account | `solidevfunctionstorage` | Storage Account | Central India | Function App storage |
| Database | `smartinvoicedb` | Cosmos DB | Global | Application data |

## Prerequisites

### Local Development Tools

1. **Azure CLI**
   ```bash
   # macOS
   brew install azure-cli
   
   # Login
   az login
   ```

2. **Azure Functions Core Tools**
   ```bash
   # macOS
   brew install azure-functions-core-tools@4
   ```

3. **Python 3.11+**
   ```bash
   python --version  # Should be 3.11 or higher
   ```

4. **Node.js & npm** (for frontend)
   ```bash
   node --version
   npm --version
   ```

## Backend API Deployment

### 1. Configure Environment Variables

Update `.env` in `smart-invoice-pro-api-2`:

```bash
# Cosmos DB
COSMOS_URI=https://smartinvoicepro.documents.azure.com:443/
COSMOS_KEY=YOUR_COSMOS_DB_KEY
COSMOS_DB_NAME=smartinvoicedb
COSMOS_CONTAINER_NAME=users

# Azure Communication Service
AZURE_EMAIL_CONNECTION_STRING=YOUR_AZURE_EMAIL_CONNECTION_STRING
SENDER_EMAIL=admin@solidevelectrosoft.com
ALERT_EMAIL=davinder@solidevelectrosoft.com
```

### 2. Deploy to Azure App Service

#### Option A: Using Azure CLI

```bash
cd smart-invoice-pro-api-2

# Build deployment package
zip -r deploy.zip . -x "*.git*" -x "*__pycache__*" -x "*.venv*"

# Deploy to Azure
az webapp deployment source config-zip \
  --resource-group solidev \
  --name smartinvoicepro \
  --src deploy.zip
```

#### Option B: Using Git Deployment

```bash
# Configure deployment credentials
az webapp deployment user set \
  --user-name <username> \
  --password <password>

# Get Git URL
az webapp deployment source show \
  --name smartinvoicepro \
  --resource-group solidev

# Deploy via Git
git remote add azure <git-url>
git push azure main
```

#### Option C: Using VS Code

1. Install "Azure App Service" extension
2. Right-click `smart-invoice-pro-api-2` folder
3. Select "Deploy to Web App..."
4. Choose `smartinvoicepro`

### 3. Configure App Service Settings

Set environment variables in Azure:

```bash
az webapp config appsettings set \
  --name smartinvoicepro \
  --resource-group solidev \
  --settings \
    AZURE_EMAIL_CONNECTION_STRING="<connection-string>" \
    SENDER_EMAIL="admin@solidevelectrosoft.com" \
    ALERT_EMAIL="davinder@solidevelectrosoft.com" \
    COSMOS_URI="https://smartinvoicepro.documents.azure.com:443/" \
    COSMOS_KEY="<cosmos-key>" \
    COSMOS_DB_NAME="smartinvoicedb"
```

Or configure via Azure Portal:
1. Navigate to App Service > Configuration > Application settings
2. Add each environment variable
3. Click "Save"

## Azure Functions Deployment

### 1. Local Testing

Before deploying, test the function locally:

```bash
cd azure-functions

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run locally
func start
```

The function will execute based on the timer schedule. To test immediately, modify the schedule in `function.json` temporarily:
```json
"schedule": "0 */5 * * * *"  // Every 5 minutes for testing
```

### 2. Deploy to Azure

```bash
# Ensure you're in the azure-functions directory
cd azure-functions

# Deploy
func azure functionapp publish smartinvoice-inventory-alerts
```

### 3. Configure Function App Settings

Set environment variables for the Function App:

```bash
az functionapp config appsettings set \
  --name smartinvoice-inventory-alerts \
  --resource-group solidev \
  --settings \
    "AZURE_EMAIL_CONNECTION_STRING=<connection-string>" \
    "SENDER_EMAIL=admin@solidevelectrosoft.com" \
    "ALERT_EMAIL=davinder@solidevelectrosoft.com" \
    "COSMOS_URI=https://smartinvoicepro.documents.azure.com:443/" \
    "COSMOS_KEY=<cosmos-key>" \
    "COSMOS_DB_NAME=smartinvoicedb"
```

### 4. Verify Deployment

Check function status in Azure Portal:
1. Navigate to Function App > Functions
2. Click on `LowStockAlert`
3. Go to "Monitor" tab
4. View execution history and logs

## Frontend Deployment

### 1. Build Frontend

```bash
cd smart-invoice-pro

# Install dependencies
npm install

# Build for production
npm run build
```

### 2. Deploy to Azure Static Web Apps

Frontend is automatically deployed via GitHub Actions when pushing to the repository.

Check deployment status:
- View `.github/workflows/azure-static-web-apps-*.yml`
- Monitor GitHub Actions tab in repository

To manually trigger:
```bash
# Push to main branch
git push origin main
```

## Email Service Configuration

### Azure Communication Services Setup

1. **Verify Domain**
   - Navigate to Azure Portal > Communication Services > Email > Domains
   - Ensure `solidevelectrosoft.com` shows "Verified" status
   - If not verified, follow Azure's domain verification process

2. **Configure Sender**
   - Add `admin@solidevelectrosoft.com` as verified sender
   - Update DNS records if prompted

3. **Get Connection String**
   ```bash
   az communication list-key \
     --name solidev-email-send-resource-3 \
     --resource-group solidev
   ```

4. **Test Email Sending**
   ```bash
   # Test from backend API
   curl -X POST http://localhost:5000/api/cron/check-low-stock?send_email=true
   
   # Or test production
   curl -X POST https://smartinvoicepro.azurewebsites.net/api/cron/check-low-stock?send_email=true
   ```

## Monitoring & Logs

### Application Insights

Function App automatically creates Application Insights. View telemetry:
1. Navigate to Function App > Application Insights
2. View:
   - Request rates
   - Response times
   - Failure rates
   - Custom events (email sends)

### Function Logs

View real-time logs:
```bash
# Azure CLI
az webapp log tail \
  --name smartinvoice-inventory-alerts \
  --resource-group solidev
```

Or in Azure Portal:
1. Function App > Functions > LowStockAlert
2. Click "Monitor"
3. View "Invocations" and "Logs"

### Email Delivery Logs

Check email sending status:
1. Azure Portal > Communication Services > `solidev-email-send-resource-3`
2. Go to "Monitoring" > "Logs"
3. Query email operations

## Scheduled Function Configuration

### Modify Schedule

Edit `azure-functions/LowStockAlert/function.json`:

```json
{
  "schedule": "0 0 9 * * *"  // Daily at 9:00 AM UTC
}
```

Schedule format: `{second} {minute} {hour} {day} {month} {day-of-week}`

**Common schedules:**
- Every 6 hours: `0 0 */6 * * *`
- Weekdays at 8 AM: `0 0 8 * * 1-5`
- Twice daily (9 AM & 5 PM): `0 0 9,17 * * *`
- Every 30 minutes: `0 */30 * * * *`

After modifying, redeploy:
```bash
func azure functionapp publish smartinvoice-inventory-alerts
```

## Security Best Practices

1. **Rotate Access Keys**
   ```bash
   # Regenerate communication service key
   az communication regenerate-key \
     --name solidev-email-send-resource-3 \
     --resource-group solidev \
     --key-type primary
   ```
   Then update `AZURE_EMAIL_CONNECTION_STRING` in all services.

2. **Enable Managed Identity** (Optional)
   - Configure Function App to use Managed Identity
   - Grant access to Cosmos DB without connection strings

3. **Restrict Network Access**
   - Configure firewall rules for Cosmos DB
   - Enable private endpoints for production

4. **Monitor Costs**
   - Set up billing alerts in Azure Portal
   - Review cost analysis monthly

## Troubleshooting

### Function Not Executing

1. **Check Function App Status**
   ```bash
   az functionapp show \
     --name smartinvoice-inventory-alerts \
     --resource-group solidev \
     --query state
   ```
   Should return "Running"

2. **Restart Function App**
   ```bash
   az functionapp restart \
     --name smartinvoice-inventory-alerts \
     --resource-group solidev
   ```

3. **Verify Timer Trigger**
   - Check logs for timer invocations
   - Ensure schedule is valid NCronTab expression

### Email Not Sending

1. **Test Connection String**
   ```python
   from azure.communication.email import EmailClient
   
   client = EmailClient.from_connection_string(CONNECTION_STRING)
   print("Connection successful")
   ```

2. **Check Domain Verification**
   - Azure Portal > Communication Services > Email > Domains
   - Ensure domain status is "Verified"

3. **Review Logs**
   ```bash
   az functionapp log download \
     --name smartinvoice-inventory-alerts \
     --resource-group solidev
   ```

### Deployment Failures

1. **Check Build Logs**
   - View Azure Portal > App Service > Deployment Center > Logs

2. **Verify Dependencies**
   ```bash
   # Test installation locally
   pip install -r requirements.txt
   ```

3. **Check Python Version**
   - Ensure Function App runtime matches local version (3.11)

## Cost Optimization

### Current Costs (Estimated)

- **Function App (Consumption Plan)**: ~$0 (free executions included)
- **Communication Services**: ~$0.001 per email
- **Storage Account**: ~$0.01/month
- **Cosmos DB**: Variable (based on usage)
- **Application Insights**: Free tier (5 GB/month)

### Optimization Tips

1. **Adjust Function Schedule**
   - Reduce frequency if daily checks aren't needed
   - Use longer intervals for non-critical alerts

2. **Email Batching**
   - Combine multiple alerts into single email
   - Set threshold (e.g., only alert if >3 products low)

3. **Cosmos DB**
   - Use manual throughput instead of autoscale for predictable workloads
   - Archive old stock transactions

## Backup & Disaster Recovery

### Database Backups

Cosmos DB automatically backs up data:
- Continuous backup enabled by default
- Point-in-time restore available
- Configure backup policy in Azure Portal

### Function App Backup

Function code is stored in Git:
```bash
# Backup function code
cd azure-functions
git add .
git commit -m "Backup function code"
git push
```

### Configuration Backup

Export app settings:
```bash
# Function App settings
az functionapp config appsettings list \
  --name smartinvoice-inventory-alerts \
  --resource-group solidev \
  > function-app-settings.json

# Web App settings
az webapp config appsettings list \
  --name smartinvoicepro \
  --resource-group solidev \
  > web-app-settings.json
```

## Support & Resources

- **Azure Functions Documentation**: https://docs.microsoft.com/azure/azure-functions/
- **Azure Communication Services**: https://docs.microsoft.com/azure/communication-services/
- **Cosmos DB Python SDK**: https://docs.microsoft.com/azure/cosmos-db/sql/sdk-python
- **Azure Portal**: https://portal.azure.com
- **Azure Status**: https://status.azure.com

## Next Steps

1. ✅ Deploy backend API to Azure App Service
2. ✅ Deploy Azure Function for scheduled alerts
3. ✅ Configure email service
4. ✅ Test end-to-end workflow
5. 🔄 Monitor for 1 week to ensure stability
6. 🔄 Set up billing alerts
7. 🔄 Configure backup strategy
8. 🔄 Document team access & responsibilities
