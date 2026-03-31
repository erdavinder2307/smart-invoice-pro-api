from flask import Flask, request
from flasgger import Swagger
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from smart_invoice_pro.api.routes import auth_blueprint, api_blueprint
from smart_invoice_pro.api.invoices import api_blueprint as invoices_blueprint
from smart_invoice_pro.api.customers_api import customers_blueprint
from smart_invoice_pro.api.invoice_generation import invoice_generation_blueprint
from smart_invoice_pro.api.product_api import product_blueprint
from smart_invoice_pro.api.stock_api import stock_blueprint
from smart_invoice_pro.api.dashboard_api import dashboard_blueprint
from smart_invoice_pro.api.bank_accounts_api import bank_accounts_blueprint
from smart_invoice_pro.api.contact_api import contact_blueprint
from smart_invoice_pro.api.profile_api import profile_blueprint
from smart_invoice_pro.api.quotes_api import quotes_blueprint
from smart_invoice_pro.api.recurring_profiles_api import recurring_profiles_blueprint
from smart_invoice_pro.api.sales_orders_api import sales_orders_blueprint
from smart_invoice_pro.api.vendors_api import vendors_blueprint
from smart_invoice_pro.api.purchase_orders_api import purchase_orders_blueprint
from smart_invoice_pro.api.bills_api import bills_blueprint
from smart_invoice_pro.api.expenses_api import expenses_blueprint
from smart_invoice_pro.api.cron_jobs import cron_blueprint
from smart_invoice_pro.api.reports_api import reports_blueprint
from smart_invoice_pro.api.payments_api import payments_blueprint
from smart_invoice_pro.api.bank_reconciliation_api import bank_reconciliation_blueprint
from smart_invoice_pro.api.roles_api import roles_blueprint
from smart_invoice_pro.api.gst_api import gst_blueprint
from smart_invoice_pro.api.reminders_api import reminders_blueprint
from smart_invoice_pro.api.organization_profile_api import org_profile_blueprint
from smart_invoice_pro.api.branding_api import branding_blueprint
from smart_invoice_pro.api.invoice_preferences_api import invoice_preferences_blueprint
from smart_invoice_pro.api.tax_rates_api import tax_rates_blueprint
from smart_invoice_pro.api.roles_permissions_api import roles_permissions_blueprint
from smart_invoice_pro.api.automation_settings_api import automation_blueprint
from smart_invoice_pro.api.integrations_settings_api import integrations_blueprint
from smart_invoice_pro.api.notifications_api import notifications_blueprint
from smart_invoice_pro.api.audit_logs_api import audit_logs_blueprint
from smart_invoice_pro.api.auth_middleware import enforce_api_auth
from smart_invoice_pro.services.scheduler import start_scheduler
import atexit

def create_app():
    app = Flask(__name__, template_folder="../templates")

    # Swagger config (optional)
    app.config['SWAGGER'] = {
        'title': 'Smart Invoice Pro API',
        'uiversion': 3
    }

    Swagger(app)

    # Rate limiting (uses in-memory storage by default; switch to Redis in production)
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[],
        storage_uri="memory://"
    )

    # Apply 5 attempts per minute to the login endpoint
    from smart_invoice_pro.api.routes import login_user
    limiter.limit("5 per minute")(login_user)

    # Enable CORS – explicit origins so that credentialed requests work correctly
    CORS(
        app,
        resources={r"/api/*": {"origins": ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080", "http://127.0.0.1:8080"]}},
        allow_headers=["Content-Type", "Authorization", "X-User-Id", "X-Username"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        supports_credentials=True,
    )

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

    @app.after_request
    def _add_cors_headers(response):
        origin = request.headers.get("Origin", "")
        allowed = {"http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8080", "http://127.0.0.1:8080"}
        if origin in allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-User-Id, X-Username"
            )
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )
        return response

    @app.before_request
    def _enforce_auth_for_all_api_routes():
        return enforce_api_auth()

    # Register your API blueprints here
    app.register_blueprint(auth_blueprint, url_prefix="/api")
    app.register_blueprint(api_blueprint, url_prefix="/api")
    app.register_blueprint(invoices_blueprint, url_prefix="/api")
    app.register_blueprint(customers_blueprint, url_prefix="/api")
    app.register_blueprint(invoice_generation_blueprint, url_prefix="/api")
    app.register_blueprint(product_blueprint, url_prefix="/api")
    app.register_blueprint(stock_blueprint, url_prefix="/api")
    app.register_blueprint(dashboard_blueprint, url_prefix="/api")
    app.register_blueprint(bank_accounts_blueprint, url_prefix="/api")
    app.register_blueprint(contact_blueprint, url_prefix="/api")
    app.register_blueprint(profile_blueprint, url_prefix="/api")
    app.register_blueprint(quotes_blueprint, url_prefix="/api")
    app.register_blueprint(recurring_profiles_blueprint, url_prefix="/api")
    app.register_blueprint(sales_orders_blueprint, url_prefix="/api")
    app.register_blueprint(vendors_blueprint, url_prefix="/api")
    app.register_blueprint(purchase_orders_blueprint, url_prefix="/api")
    app.register_blueprint(bills_blueprint, url_prefix="/api")
    app.register_blueprint(expenses_blueprint, url_prefix="/api")
    app.register_blueprint(cron_blueprint, url_prefix="/api")
    app.register_blueprint(reports_blueprint, url_prefix="/api")
    app.register_blueprint(payments_blueprint, url_prefix="/api")
    app.register_blueprint(bank_reconciliation_blueprint, url_prefix="/api")
    app.register_blueprint(roles_blueprint, url_prefix="/api")
    app.register_blueprint(gst_blueprint, url_prefix="/api")
    app.register_blueprint(reminders_blueprint, url_prefix="/api")
    app.register_blueprint(org_profile_blueprint, url_prefix="/api")
    app.register_blueprint(branding_blueprint, url_prefix="/api")
    app.register_blueprint(invoice_preferences_blueprint, url_prefix="/api")
    app.register_blueprint(tax_rates_blueprint, url_prefix="/api")
    app.register_blueprint(roles_permissions_blueprint, url_prefix="/api")
    app.register_blueprint(automation_blueprint, url_prefix="/api")
    app.register_blueprint(integrations_blueprint, url_prefix="/api")
    app.register_blueprint(notifications_blueprint, url_prefix="/api")
    app.register_blueprint(audit_logs_blueprint, url_prefix="/api")

    # Start the background scheduler for recurring invoices
    try:
        start_scheduler(app)
        
        # Register cleanup on app shutdown
        @atexit.register
        def cleanup():
            from smart_invoice_pro.services.scheduler import shutdown_scheduler
            shutdown_scheduler(app)
    except Exception as e:
        print(f"Warning: Could not start background scheduler: {e}")

    return app
