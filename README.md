# Smart Invoice Pro API

Smart Invoice Pro provides a simple, RESTful endpoint to generate professional invoice PDFs from JSON data. This README guides you through installation, usage, and example requests.

## Key Features
- Generate styled invoice PDFs on demand
- Customizable invoice details (customer info, line items, taxes, totals)
- No external PDF service required – self-hosted using ReportLab
- API documentation via Swagger (Flasgger)

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

## Deployment
- Deploy the Flask app behind a WSGI server (e.g., Gunicorn).
- Mount behind Nginx or any reverse proxy for production use.

## Support
For issues or questions, please open an issue on the repository or contact the development team.
