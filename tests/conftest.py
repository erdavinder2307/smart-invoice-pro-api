"""
Shared pytest fixtures for Smart Invoice Pro backend tests.

All Cosmos DB containers are mocked at the module level so that
no test ever hits a real database.
"""
import datetime
import sys
import uuid
from unittest.mock import MagicMock, patch

import jwt
import pytest

# ── Mock CosmosClient before any application module is imported ─────────────
# cosmos_client.py creates a real CosmosClient at import time, which fails
# without valid credentials. We patch it early so all containers become mocks.
_cosmos_client_patcher = patch(
    "azure.cosmos.CosmosClient",
    return_value=MagicMock(),
)
_cosmos_client_patcher.start()

from smart_invoice_pro.app import create_app

# ── JWT secret must match the one in auth_middleware.py ──────────────────────
JWT_SECRET = "your_secret_key"

# ── Tenant / user identifiers ───────────────────────────────────────────────
TENANT_A = "tenant-aaa-1111"
USER_A = "user-aaa-1111"

TENANT_B = "tenant-bbb-2222"
USER_B = "user-bbb-2222"


# ── Token helpers ────────────────────────────────────────────────────────────
def make_token(user_id=USER_A, tenant_id=TENANT_A, **overrides):
    """Generate a valid JWT token for testing."""
    payload = {
        "id": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "username": "testuser",
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    }
    payload.update(overrides)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def make_expired_token(user_id=USER_A, tenant_id=TENANT_A):
    """Generate an expired JWT token."""
    payload = {
        "id": user_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "username": "testuser",
        "exp": datetime.datetime.utcnow() - datetime.timedelta(hours=1),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def auth_headers(user_id=USER_A, tenant_id=TENANT_A, **kw):
    """Return Authorization header dict for the given user/tenant."""
    token = make_token(user_id=user_id, tenant_id=tenant_id, **kw)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Container mock factory ──────────────────────────────────────────────────
def _mock_container():
    """Create a MagicMock that behaves like an Azure Cosmos container."""
    c = MagicMock()
    c.query_items.return_value = []
    c.read_all_items.return_value = []
    return c


# ── The big list of container patches ───────────────────────────────────────
# Each entry is the full dotted path to the container object that needs mocking
# in a given API module.
_CONTAINER_PATCHES = [
    # Auth routes
    "smart_invoice_pro.api.routes.users_container",
    "smart_invoice_pro.api.routes.refresh_tokens_container",
    # Invoices
    "smart_invoice_pro.api.invoices.invoices_container",
    "smart_invoice_pro.api.invoices.get_container",
    # Invoice preferences (used inside invoice create)
    "smart_invoice_pro.api.invoice_preferences_api.settings_container",
    # Tax rates (used inside invoice create)
    "smart_invoice_pro.api.tax_rates_api.settings_container",
    # Customers
    "smart_invoice_pro.api.customers_api.customers_container",
    "smart_invoice_pro.api.customers_api.invoices_container",
    # Products
    "smart_invoice_pro.api.product_api.products_container",
    "smart_invoice_pro.api.product_api.get_container",
    # Stock
    "smart_invoice_pro.api.stock_api.stock_container",
    # Payments
    "smart_invoice_pro.api.payments_api.get_container",
    # Dashboard
    "smart_invoice_pro.api.dashboard_api.invoices_container",
    "smart_invoice_pro.api.dashboard_api.customers_container",
    "smart_invoice_pro.api.dashboard_api.products_container",
    "smart_invoice_pro.api.dashboard_api.get_container",
    # Webhook dispatcher (prevent real HTTP calls)
    "smart_invoice_pro.utils.webhook_dispatcher.settings_container",
    # Notifications
    "smart_invoice_pro.utils.notifications.notifications_container",
    # Audit logger
    "smart_invoice_pro.utils.audit_logger.audit_logs_container",
    # Settings pages
    "smart_invoice_pro.api.reminders_api.settings_container",
    "smart_invoice_pro.api.organization_profile_api.settings_container",
    "smart_invoice_pro.api.branding_api.settings_container",
    "smart_invoice_pro.api.automation_settings_api.settings_container",
    "smart_invoice_pro.api.integrations_settings_api.settings_container",
    # Roles
    "smart_invoice_pro.api.roles_api.users_container",
    "smart_invoice_pro.api.roles_api.invoices_container",
    # Notifications API
    "smart_invoice_pro.api.notifications_api.notifications_container",
    # Audit logs API
    "smart_invoice_pro.api.audit_logs_api.audit_logs_container",
    # Quotes
    "smart_invoice_pro.api.quotes_api.quotes_container",
    "smart_invoice_pro.api.quotes_api.get_container",
    # Vendors
    "smart_invoice_pro.api.vendors_api.vendors_container",
    # Bills
    "smart_invoice_pro.api.bills_api.bills_container",
    "smart_invoice_pro.api.bills_api.get_container",
    # Expenses
    "smart_invoice_pro.api.expenses_api.expenses_container",
    # Bank accounts
    "smart_invoice_pro.api.bank_accounts_api.bank_accounts_container",
    # Recurring profiles
    "smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container",
    # Sales orders
    "smart_invoice_pro.api.sales_orders_api.sales_orders_container",
    # Purchase orders
    "smart_invoice_pro.api.purchase_orders_api.purchase_orders_container",
    "smart_invoice_pro.api.purchase_orders_api.get_container",
    # Profile
    "smart_invoice_pro.api.profile_api.users_container",
    # Bank reconciliation
    "smart_invoice_pro.api.bank_reconciliation_api.bank_accounts_container",
    "smart_invoice_pro.api.bank_reconciliation_api.bank_txns_container",
    "smart_invoice_pro.api.bank_reconciliation_api.invoices_container",
    "smart_invoice_pro.api.bank_reconciliation_api.expenses_container",
    # Roles (purchase orders for approval workflow)
    "smart_invoice_pro.api.roles_api.purchase_orders_container",
    # Roles permissions
    "smart_invoice_pro.api.roles_permissions_api.users_container",
    "smart_invoice_pro.api.roles_permissions_api.get_container",
    # Payments
    "smart_invoice_pro.api.payments_api.invoices_container",
    "smart_invoice_pro.api.payments_api.payments_container",
    # GST
    "smart_invoice_pro.api.gst_api.customers_container",
    # Cron (uses get_container inside function, so mock the factory)
    "smart_invoice_pro.api.cron_jobs.get_container",
]


@pytest.fixture()
def app():
    """Create Flask app with all container objects mocked."""
    patchers = []
    mocks = {}
    for target in _CONTAINER_PATCHES:
        try:
            p = patch(target, new_callable=_mock_container)
            mocks[target] = p.start()
            patchers.append(p)
        except Exception:
            # Some patches may not resolve if the module layout changes;
            # that's OK — tests that need them will patch explicitly.
            pass

    application = create_app()
    application.config["TESTING"] = True

    yield application

    for p in patchers:
        p.stop()


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture()
def headers_a():
    """Auth headers for tenant A (default test tenant)."""
    return auth_headers(user_id=USER_A, tenant_id=TENANT_A)


@pytest.fixture()
def headers_b():
    """Auth headers for tenant B (cross-tenant testing)."""
    return auth_headers(user_id=USER_B, tenant_id=TENANT_B)


# ── Sample data factories ───────────────────────────────────────────────────
@pytest.fixture()
def sample_customer():
    """Minimal valid customer payload."""
    return {
        "display_name": "Acme Corp",
        "email": "acme@example.com",
        "phone": "9876543210",
        "customer_type": "business",
        "company_name": "Acme Corp Pvt Ltd",
    }


@pytest.fixture()
def sample_product():
    """Minimal valid product payload."""
    return {
        "name": "Widget Pro",
        "price": 500.0,
        "unit": "Nos",
        "sales_enabled": True,
        "purchase_enabled": True,
    }


@pytest.fixture()
def sample_invoice():
    """Minimal valid invoice payload."""
    return {
        "invoice_number": "INV-TEST-001",
        "customer_id": "cust-001",
        "customer_name": "Acme Corp",
        "issue_date": "2025-06-01",
        "due_date": "2025-06-15",
        "subtotal": 1000.0,
        "total_amount": 1000.0,
        "status": "Draft",
        "items": [],
    }


@pytest.fixture()
def stored_invoice_a():
    """A pre-existing invoice document owned by tenant A."""
    return {
        "id": "inv-aaa-001",
        "invoice_number": "INV-001",
        "customer_id": "cust-001",
        "customer_name": "Acme Corp",
        "issue_date": "2025-06-01",
        "due_date": "2025-06-15",
        "subtotal": 1000.0,
        "total_amount": 1180.0,
        "amount_paid": 0.0,
        "balance_due": 1180.0,
        "status": "Issued",
        "tenant_id": TENANT_A,
        "items": [],
        "payment_history": [],
        "created_at": "2025-06-01T00:00:00",
        "updated_at": "2025-06-01T00:00:00",
    }


@pytest.fixture()
def stored_customer_a():
    """A pre-existing customer document owned by tenant A."""
    return {
        "id": "cust-aaa-001",
        "customer_id": "cust-pk-001",
        "display_name": "Acme Corp",
        "email": "acme@example.com",
        "phone": "9876543210",
        "tenant_id": TENANT_A,
        "created_at": "2025-06-01T00:00:00",
        "updated_at": "2025-06-01T00:00:00",
    }


@pytest.fixture()
def stored_product_a():
    """A pre-existing product document owned by tenant A."""
    return {
        "id": "prod-aaa-001",
        "product_id": "prod-pk-001",
        "name": "Widget Pro",
        "price": 500.0,
        "unit": "Nos",
        "tenant_id": TENANT_A,
        "is_deleted": False,
        "deleted_at": None,
        "created_at": "2025-06-01T00:00:00",
        "updated_at": "2025-06-01T00:00:00",
    }
