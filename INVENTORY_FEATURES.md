# Advanced Inventory Management Features

## Overview

This document describes the advanced inventory management features that have been implemented in the Smart Invoice Pro application.

## Features Implemented

### 1. **Reorder Level & Quantity Management**

Products now support automatic restocking with configurable reorder levels and quantities.

#### Backend Changes:
- **Product Model Updates** (`product_api.py`):
  - Added `reorder_level`: Threshold at which low stock alerts trigger
  - Added `reorder_qty`: Default quantity to order when restocking
  - Added `preferred_vendor_id`: Auto-selected vendor for restock orders

#### Frontend Changes:
- **AddEditProduct Component** (`AddEditProduct.jsx`):
  - New "Inventory Management" section with:
    - Reorder Level input field
    - Reorder Quantity input field
    - Preferred Vendor dropdown (auto-populated from vendors)

### 2. **Low Stock Highlighting & Filtering**

Products with low inventory are now visually highlighted and can be filtered.

#### Frontend Changes:
- **ProductList Component** (`ProductList.jsx`):
  - **Visual Highlighting**:
    - Yellow background (warning.50) for low stock items (stock ≤ reorder_level)
    - Red background (error.50) for out of stock items (stock = 0)
  - **Filter Options**:
    - New "Filter by Stock" dropdown with:
      - All Stock Levels
      - In Stock
      - Low Stock
      - Out of Stock
  - Uses product-specific reorder_level (defaults to 10 if not set)

### 3. **One-Click Restock with Auto-PO Generation**

Products can be restocked automatically by generating purchase orders.

#### Backend Endpoint:
```
POST /api/products/{product_id}/restock
```

**Request Body** (optional):
```json
{
  "quantity": 100,      // Override reorder_qty
  "vendor_id": "uuid"   // Override preferred_vendor_id
}
```

**Response**:
```json
{
  "message": "Purchase order created successfully",
  "po_id": "uuid",
  "po_number": "PO-001",
  "vendor_id": "vendor-uuid",
  "product_id": "product-uuid",
  "quantity": 50,
  "total_amount": 5000
}
```

**Logic**:
- Uses product's preferred vendor and reorder quantity
- Calculates PO number automatically
- Creates PO in "Draft" status
- Includes tax calculations based on product tax rate
- Marks PO as auto-generated

#### Frontend:
- **ProductList Component**:
  - "Restock (Generate PO)" menu item in action menu
  - Only visible if product has preferred_vendor_id set
  - Shows success alert with PO number on completion

### 4. **Automatic Stock Updates**

Stock levels automatically update when transactions occur.

#### Invoice Creation (`invoices.py`):
- **Decrements stock** when invoice is created
- Creates stock transaction with:
  - Type: `OUT`
  - Source: `Invoice {invoice_number}`
  - Reference ID: invoice_id

#### Bill Creation (`bills_api.py`):
- **Increments stock** when bill is created
- Creates stock transaction with:
  - Type: `IN`
  - Source: `Bill {bill_number}`
  - Reference ID: bill_id

### 5. **Low Stock Monitoring & Email Alerts** ✨ *Azure-Powered*

Automated daily checks for low stock items with email notifications using **Azure Communication Services**.

#### Backend Endpoint:
```
GET/POST /api/cron/check-low-stock?send_email=true
```

**Response**:
```json
{
  "message": "Low stock check completed",
  "low_stock_count": 3,
  "products": [
    {
      "id": "uuid",
      "name": "Product A",
      "current_stock": 5,
      "reorder_level": 10,
      "reorder_qty": 50,
      "preferred_vendor_id": "vendor-uuid"
    }
  ],
  "email_sent": true,
  "timestamp": "2026-02-28T12:00:00Z"
}
```

#### Email Service:
- **Provider**: Azure Communication Services (`solidev-email-send-resource-3`)
- **Sender**: admin@solidevelectrosoft.com (verified domain)
- **Implementation**: Uses `azure.communication.email.EmailClient`

#### Email Alert Features:
- HTML formatted email with product table
- Color-coded: Yellow for low stock, Red for out of stock
- Shows current stock, reorder level, and recommended order quantity
- Lists all products below reorder level
- Fully managed by Azure (no SMTP configuration needed)

### 6. **Manual Inventory Adjustments**

Existing inventory adjustment endpoint supports manual stock corrections.

#### Endpoint:
```
POST /api/stock/adjust
```

**Request Body**:
```json
{
  "product_id": "uuid",
  "type": "Damage|Loss|Found|Manual",
  "quantity": 10,
  "reason": "Damaged during shipment",
  "reference_number": "ADJ-001",
  "unit_cost": 100.50,
  "location": "Main Warehouse",
  "adjustment_date": "2026-02-28",
  "user_id": "user-uuid"
}
```

## Setup Instructions

### Environment Variables

Add these to your `.env` file:

```bash
# Azure Communication Service (Email)
AZURE_EMAIL_CONNECTION_STRING=endpoint=https://solidev-email-send-resource-3.india.communication.azure.com/;accesskey=YOUR_ACCESS_KEY
SENDER_EMAIL=admin@solidevelectrosoft.com
ALERT_EMAIL=davinder@solidevelectrosoft.com

# Cosmos DB (Already configured)
COSMOS_URI=https://smartinvoicepro.documents.azure.com:443/
COSMOS_KEY=your-cosmos-key
COSMOS_DB_NAME=smartinvoicedb
```

#### Azure Communication Service Setup

The email service is already configured:
- **Resource**: `solidev-email-send-resource-3` (Central India)
- **Domain**: solidevelectrosoft.com (verified)
- **Sender**: admin@solidevelectrosoft.com

To get the connection string:
```bash
az communication list-key --name solidev-email-send-resource-3 --resource-group solidev
```

### Automated Scheduling with Azure Functions ⚡

**Azure Function App**: `smartinvoice-inventory-alerts` (East US)

A timer-triggered Azure Function automatically checks for low stock daily at 9:00 AM UTC.

#### Function Details:
- **Name**: `LowStockAlert`
- **Trigger Type**: Timer (Cron)
- **Schedule**: `0 0 9 * * *` (Daily at 9:00 AM UTC)
- **Service**: Serverless (Consumption Plan)
- **Location**: East US (Linux)

#### Local Development:

1. Install Azure Functions Core Tools:
   ```bash
   brew install azure-functions-core-tools@4
   ```

2. Navigate to function directory:
   ```bash
   cd azure-functions
   ```

3. Install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. Run locally:
   ```bash
   func start
   ```

#### Deployment:

Deploy from the `azure-functions` directory:
```bash
func azure functionapp publish smartinvoice-inventory-alerts
```

Or use VS Code:
1. Install Azure Functions extension
2. Right-click `azure-functions` folder
3. Select "Deploy to Function App..."
4. Choose `smartinvoice-inventory-alerts`

#### Monitoring:

View execution history in Azure Portal:
- Navigate to: Function App > Functions > LowStockAlert > Monitor
- View logs in Application Insights
- Check email delivery status in Communication Services logs

For detailed Azure Functions documentation, see [azure-functions/README.md](./azure-functions/README.md)

## API Endpoints Summary

### Inventory Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/products` | POST/PUT | Create/update product with reorder fields |
| `/api/products/low-stock` | GET | Get all products with low stock |
| `/api/products/{id}/restock` | POST | Generate PO for restocking |
| `/api/stock/adjust` | POST | Manual inventory adjustment |
| `/api/cron/check-low-stock` | GET/POST | Check low stock and send alerts |
| `/api/cron/schedule-info` | GET | Get cron job configuration info |

## Testing the Features

### 1. Test Reorder Level Alerts

```bash
# Set a product with low stock
curl -X PUT http://localhost:5000/api/products/{product_id} \
  -H "Content-Type: application/json" \
  -d '{
    "reorder_level": 50,
    "reorder_qty": 100
  }'

# Check low stock
curl http://localhost:5000/api/products/low-stock
```

### 2. Test Auto-Restock

```bash
# Restock a product
curl -X POST http://localhost:5000/api/products/{product_id}/restock
```

### 3. Test Email Alerts

```bash
# Trigger low stock check with email
curl "http://localhost:5000/api/cron/check-low-stock?send_email=true"
```

### 4. Test Stock Auto-Update

```bash
# Create an invoice - stock should decrement
curl -X POST http://localhost:5000/api/invoices \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_number": "INV-001",
    "customer_id": "123",
    "items": [
      {
        "product_id": "product-uuid",
        "quantity": 5,
        "rate": 100
      }
    ],
    ...
  }'

# Create a bill - stock should increment
curl -X POST http://localhost:5000/api/bills \
  -H "Content-Type: application/json" \
  -d '{
    "bill_number": "BILL-001",
    "vendor_id": "vendor-uuid",
    "items": [
      {
        "product_id": "product-uuid",
        "quantity": 50,
        "rate": 100
      }
    ],
    ...
  }'
```

## Database Schema Changes

### Products Container
```json
{
  "id": "uuid",
  "name": "Product A",
  "price": 100,
  "reorder_level": 10,        // NEW
  "reorder_qty": 50,           // NEW
  "preferred_vendor_id": "uuid" // NEW
}
```

### Stock Container (Enhanced)
```json
{
  "id": "uuid",
  "product_id": "uuid",
  "quantity": 50,
  "type": "IN|OUT",
  "source": "Invoice INV-001|Bill BILL-001",
  "reference_id": "invoice_id|bill_id",  // NEW
  "timestamp": "2026-02-28T12:00:00Z"
}
```

## Best Practices

1. **Set Reorder Levels**: Configure reorder_level for all critical products
2. **Assign Preferred Vendors**: Set preferred_vendor_id for one-click restocking
3. **Monitor Email Alerts**: Review daily low stock emails
4. **Regular Stock Audits**: Use inventory adjustment endpoint for physical counts
5. **Review Auto-Generated POs**: Check draft POs before finalizing

## Troubleshooting

### Email Not Sending
- **Check Connection String**: Verify `AZURE_EMAIL_CONNECTION_STRING` in environment variables
- **Verify Domain**: Ensure solidevelectrosoft.com domain is verified in Azure Communication Services
- **Check Sender**: Confirm `admin@solidevelectrosoft.com` is a valid sender in Azure portal
- **View Logs**: Check Azure Function logs or backend API logs for email sending errors
- **Test Endpoint**: Manually trigger `/api/cron/check-low-stock?send_email=true`

### Azure Function Not Running
- **Check Schedule**: Verify timer trigger in `azure-functions/LowStockAlert/function.json`
- **Function App Status**: Ensure Function App is running (not stopped) in Azure Portal
- **Monitor Logs**: Navigate to Function App > Monitor > Logs
- **Configuration**: Verify all app settings are configured correctly
- **Trigger Manually**: Test function locally with `func start` or use "Test/Run" in Azure Portal

### Stock Not Updating
- Verify invoice/bill includes `items` array with `product_id`
- Check stock transactions in database
- Review API logs for errors during transaction creation

### Restock Button Not Appearing
- Ensure product has `preferred_vendor_id` set
- Verify vendor still exists in system
- Check browser console for JavaScript errors

### Azure Communication Service Issues
- **Connection Error**: Verify connection string format and access key
- **Domain Not Verified**: Check Azure Portal > Communication Services > Email > Domains
- **Rate Limiting**: Check if email quota is exceeded (review Azure Portal metrics)
- **Delivery Failures**: View email operation logs in Azure Communication Services

## Future Enhancements

Potential additions to consider:
- Multi-location inventory tracking
- Batch restock for multiple products
- Predictive reordering based on sales trends
- Integration with supplier APIs for automated ordering
- Mobile app for stock counting
- Barcode/QR code scanning
