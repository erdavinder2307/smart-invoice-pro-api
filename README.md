# Smart Invoice Pro API

Smart Invoice Pro provides a comprehensive backend API for invoice management, inventory tracking, and automated notifications powered by Azure cloud services.

## Key Features
- 📄 Generate professional invoice PDFs on demand
- 📦 Advanced inventory management with reorder levels
- 📧 Automated low stock email alerts (Azure Communication Services)
- ⏰ Scheduled inventory monitoring (Azure Functions)
- 💾 Cosmos DB integration for scalable data storage
- 📊 Complete CRUD APIs for invoices, products, customers, and more
- 🔐 Secure Azure-based architecture
- 📚 API documentation via Swagger (Flasgger)

## Requirements
- Python 3.9+
- Virtual environment (venv or conda)
- Key dependencies installed in `requirements.txt`:
  - Flask
  - Flasgger
  - ReportLab
  - python-dotenv

## Installation
```bash
# Clone the repository
git clone <repo-url>
cd smart-invoice-pro-api

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running Locally
```bash
# Export Flask app entrypoint
export FLASK_APP=smart_invoice_pro.api.invoice_generation

# (Optional) Load .env variables
# export FLASK_ENV=development

# Start the server
flask run --host=0.0.0.0 --port=5000
```

By default, the API is now available at `http://localhost:5000`.

## API Endpoint

### Generate Invoice PDF

- **URL**: `/generate-invoice-pdf`
- **Method**: `POST`
- **Content-Type**: `application/json`
- **Response**: `application/pdf` binary

#### Request Body
```json
{
  "invoice": {
    "invoice_number": "INV-123",
    "customer_id": 456,
    "issue_date": "2025-07-17",
    "due_date": "2025-07-30",
    "payment_terms": "Net 15",
    "subtotal": 1200.00,
    "cgst_amount": 100.00,
    "sgst_amount": 100.00,
    "igst_amount": 0.00,
    "total_tax": 200.00,
    "total_amount": 1400.00,
    "amount_paid": 0.00,
    "balance_due": 1400.00,
    "status": "Pending",
    "payment_mode": "Bank Transfer",
    "notes": "Thank you for your business!",
    "terms_conditions": "Payment due within 15 days.",
    "is_gst_applicable": true
  }
}
```

#### Example Curl
```bash
curl -X POST http://localhost:5000/generate-invoice-pdf \
     -H "Content-Type: application/json" \
     -d @invoice.json --output invoice.pdf
```
- Replace `invoice.json` with your JSON file.
- The response will be saved as `invoice.pdf`.

## API Documentation
Visit `http://localhost:5000/apidocs/` to explore the interactive Swagger UI powered by Flasgger.

## Azure Integration ☁️

This project leverages Azure cloud services for production deployment:

### Azure Resources
- **Azure App Service**: Backend API hosting (`smartinvoicepro`)
- **Azure Functions**: Automated inventory alerts (`smartinvoice-inventory-alerts`)
- **Azure Communication Services**: Email notifications (`solidev-email-send-resource-3`)
- **Azure Cosmos DB**: NoSQL database (`smartinvoicedb`)
- **Application Insights**: Monitoring and telemetry

### Azure Setup
1. **Configure Environment Variables** (see `.env.example`)
   ```bash
   AZURE_EMAIL_CONNECTION_STRING=endpoint=https://...;accesskey=...
   SENDER_EMAIL=admin@solidevelectrosoft.com
   ALERT_EMAIL=davinder@solidevelectrosoft.com
   COSMOS_URI=https://smartinvoicepro.documents.azure.com:443/
   COSMOS_KEY=your-key
   COSMOS_DB_NAME=smartinvoicedb
   ```

2. **Deploy Azure Function** (Automated Low Stock Alerts)
   ```bash
   ./deploy-function.sh
   ```
   Or manually:
   ```bash
   cd azure-functions
   func azure functionapp publish smartinvoice-inventory-alerts
   ```

### Documentation
- 📋 **[INVENTORY_FEATURES.md](./INVENTORY_FEATURES.md)** - Complete inventory management features
- ☁️ **[AZURE_DEPLOYMENT.md](./AZURE_DEPLOYMENT.md)** - Full Azure deployment guide
- 🔄 **[AZURE_MIGRATION_SUMMARY.md](./AZURE_MIGRATION_SUMMARY.md)** - Azure migration details
- ⚡ **[azure-functions/README.md](./azure-functions/README.md)** - Azure Functions documentation

## Deployment
### Local Development
```bash
python app.py
# API available at http://localhost:5000
```

### Production (Azure)
- **Backend API**: Deployed to Azure App Service
- **Scheduled Tasks**: Deployed to Azure Functions
- **Database**: Azure Cosmos DB (globally distributed)
- **Email**: Azure Communication Services

Deploy backend:
```bash
az webapp deployment source config-zip \
  --resource-group solidev \
  --name smartinvoicepro \
  --src deploy.zip
```

Deploy functions:
```bash
./deploy-function.sh
```

See [AZURE_DEPLOYMENT.md](./AZURE_DEPLOYMENT.md) for complete deployment instructions.

## Inventory Features 📦

Advanced inventory management with:
- **Reorder Levels**: Automatic low stock detection
- **Auto-Stock Updates**: Inventory adjusts on invoices/bills
- **One-Click Restock**: Generate purchase orders automatically
- **Email Alerts**: Daily low stock notifications (Azure-powered)
- **Visual Highlighting**: Low stock items highlighted in UI

See [INVENTORY_FEATURES.md](./INVENTORY_FEATURES.md) for detailed documentation.

## Support
For issues or questions:
- Open an issue on the repository
- Contact: davinder@solidevelectrosoft.com
- Azure Portal: https://portal.azure.com
