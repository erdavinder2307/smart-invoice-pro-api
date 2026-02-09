from flask import Flask
from flasgger import Swagger
from flask_cors import CORS
from smart_invoice_pro.api.routes import auth_blueprint
from smart_invoice_pro.api.invoices import api_blueprint as invoices_blueprint
from smart_invoice_pro.api.customers_api import customers_blueprint
from smart_invoice_pro.api.invoice_generation import invoice_generation_blueprint
from smart_invoice_pro.api.product_api import product_blueprint
from smart_invoice_pro.api.stock_api import stock_blueprint
from smart_invoice_pro.api.dashboard_api import dashboard_blueprint
from smart_invoice_pro.api.bank_accounts_api import bank_accounts_blueprint
from smart_invoice_pro.api.contact_api import contact_blueprint

def create_app():
    app = Flask(__name__, template_folder="../templates")

    # Swagger config (optional)
    app.config['SWAGGER'] = {
        'title': 'Smart Invoice Pro API',
        'uiversion': 3
    }

    Swagger(app)

    # Enable CORS for the Flask app (allow all origins)
    CORS(app, resources={r"/*": {"origins": "*"}})

    @app.route('/')
    def home():
        """
        Root endpoint to test Swagger.
        ---
        responses:
          200:
            description: Returns a simple message
        """
        return "Smart Invoice Pro API is running!"

    # Register your API blueprints here
    app.register_blueprint(auth_blueprint, url_prefix="/api")
    app.register_blueprint(invoices_blueprint, url_prefix="/api")
    app.register_blueprint(customers_blueprint, url_prefix="/api")
    app.register_blueprint(invoice_generation_blueprint, url_prefix="/api")
    app.register_blueprint(product_blueprint, url_prefix="/api")
    app.register_blueprint(stock_blueprint, url_prefix="/api")
    app.register_blueprint(dashboard_blueprint, url_prefix="/api")
    app.register_blueprint(bank_accounts_blueprint, url_prefix="/api")
    app.register_blueprint(contact_blueprint, url_prefix="/api")

    return app
