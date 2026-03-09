# Azure Functions - Low Stock Alert

This Azure Function sends automated daily email alerts for low stock inventory items.

## Function Overview

### LowStockAlert (Timer Trigger)
- **Schedule**: Daily at 9:00 AM UTC (`0 0 9 * * *`)
- **Purpose**: Monitors inventory levels and sends email alerts for products at or below reorder levels
- **Email Service**: Azure Communication Services

## Features

- Queries Cosmos DB for products with `availableQty <= reorder_level`
- Sends HTML-formatted email with product details
- Color-coded status:
  - 🔴 **OUT OF STOCK** (red) - availableQty = 0
  - ⚠️ **Low Stock** (yellow) - 0 < availableQty <= reorder_level

## Configuration

### Required Environment Variables

Set these in Azure Function App Settings:

```bash
AZURE_EMAIL_CONNECTION_STRING=endpoint=https://...;accesskey=...
SENDER_EMAIL=admin@solidevelectrosoft.com
ALERT_EMAIL=davinder@solidevelectrosoft.com
COSMOS_URI=https://smartinvoicepro.documents.azure.com:443/
COSMOS_KEY=your-cosmos-db-key
COSMOS_DB_NAME=smartinvoicedb
```

## Local Development

1. Install Azure Functions Core Tools:
   ```bash
   brew install azure-functions-core-tools@4
   ```

2. Install Python dependencies:
   ```bash
   cd azure-functions
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```

3. Update `local.settings.json` with your credentials

4. Run locally:
   ```bash
   func start
   ```

## Deployment to Azure

### Option 1: Using Azure CLI

```bash
# Navigate to the function directory
cd azure-functions

# Deploy to Azure
func azure functionapp publish smartinvoice-inventory-alerts
```

### Option 2: Using VS Code

1. Install Azure Functions extension
2. Right-click on `azure-functions` folder
3. Select "Deploy to Function App..."
4. Choose `smartinvoice-inventory-alerts`

## Timer Schedule Format

The schedule uses NCronTab expressions:
```
{second} {minute} {hour} {day} {month} {day-of-week}
```

Current schedule: `0 0 9 * * *`
- Runs daily at 9:00 AM UTC
- To change: Edit `schedule` in `LowStockAlert/function.json`

### Schedule Examples:
- Every 6 hours: `0 0 */6 * * *`
- Every weekday at 8 AM: `0 0 8 * * 1-5`
- Twice daily (9 AM & 5 PM): `0 0 9,17 * * *`

## Monitoring

View logs in Azure Portal:
1. Navigate to Function App > Functions > LowStockAlert
2. Click "Monitor" to see execution logs
3. View Application Insights for detailed telemetry

## Email Template

The alert email includes:
- Total count of low stock products
- Table with:
  - Product name
  - Current stock quantity
  - Reorder level threshold
  - Recommended order quantity
  - Visual status indicator

## Troubleshooting

### Email not sending
- Verify `AZURE_EMAIL_CONNECTION_STRING` is correct
- Check sender email domain is verified in Azure Communication Services
- Review function logs for email sending errors

### Function not triggering
- Check timer schedule in `function.json`
- Verify Function App is running (not stopped)
- Check "Monitor" tab for execution history

### Database connection issues
- Verify `COSMOS_URI` and `COSMOS_KEY` are correct
- Ensure Cosmos DB firewall allows Azure services
- Check network connectivity from Function App

## Dependencies

- `azure-functions`: Azure Functions Python worker
- `azure-communication-email`: Email sending client
- `azure-cosmos`: Cosmos DB Python SDK

## Cost Considerations

- **Azure Functions (Consumption Plan)**: Pay per execution (~free tier covers millions of executions)
- **Cosmos DB**: Minimal read operations (1 query per day)
- **Communication Services**: Email pricing per message sent
- **Storage Account**: Minimal cost for function state

## Related Documentation

- [Azure Functions Timer Trigger](https://docs.microsoft.com/azure/azure-functions/functions-bindings-timer)
- [Azure Communication Services Email](https://docs.microsoft.com/azure/communication-services/concepts/email/email-overview)
- [Cosmos DB Python SDK](https://docs.microsoft.com/azure/cosmos-db/sql/sdk-python)
