# run.py - entry point for Azure App Service
from smart_invoice_pro.app import create_app

# Create the Flask app (for gunicorn/uvicorn)
app = create_app()

# Flasgger/Swagger UI is set up in the app factory
# This file is compatible with both gunicorn and Azure's python run.py

if __name__ == "__main__":
    # For local development only; Azure/gunicorn will use 'app' variable
    app.run(host="0.0.0.0", port=8000)

