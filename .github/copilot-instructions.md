# Smart Invoice Pro API - Copilot Instructions

## Project Overview

Flask-based REST API backend for Smart Invoice Pro, using Azure Cosmos DB for storage and JWT (HS256) for authentication.

## Architecture

- **Framework**: Flask with Blueprints
- **Database**: Azure Cosmos DB (NoSQL)
- **Authentication**: JWT Bearer tokens (HS256)
- **All blueprints** are registered in `smart_invoice_pro/app.py` with `url_prefix="/api"`
- **Auth middleware**: `smart_invoice_pro/api/auth_middleware.py` — `enforce_api_auth()` runs as `@app.before_request`, decodes JWT, and sets `request.user_id` and `request.tenant_id`

## Key Conventions

### Route Definitions

All route decorators must include the resource name prefix (e.g., `/invoices`, `/customers`). The blueprint `url_prefix="/api"` only provides the `/api` prefix. Do NOT rely on blueprint registration for the resource path.

```python
# CORRECT — route includes resource path
@invoices_blueprint.route('/invoices', methods=['GET'])

# WRONG — missing resource path, produces /api instead of /api/invoices
@invoices_blueprint.route('', methods=['GET'])
```

### Authentication in Route Handlers

Always use the JWT context set by the auth middleware. Never use `X-User-Id` or `X-Username` headers.

```python
# CORRECT — use JWT context from middleware
user_id = request.user_id
tenant_id = request.tenant_id

# Also acceptable with fallback
user_id = getattr(request, 'user_id', None)

# WRONG — do not use custom headers for auth
user_id = request.headers.get('X-User-Id')  # DON'T DO THIS
```

### Cosmos DB Container Access

Import pre-created container instances from `cosmos_client.py`. Do NOT call `get_container()` directly — it requires a partition key argument and creates/gets the container each time.

```python
# CORRECT — import pre-created containers
from smart_invoice_pro.utils.cosmos_client import invoices_container, customers_container

# WRONG — get_container requires 2 args (name, partition_key)
container = get_container('invoices')  # DON'T DO THIS — missing partition_key
```

### Blueprint Registration

When adding a new blueprint:
1. Define it in `smart_invoice_pro/api/<name>_api.py`
2. Import and register in `smart_invoice_pro/app.py` with `url_prefix="/api"`
3. Ensure the blueprint name (first arg to `Blueprint()`) is unique across all blueprints
4. Auth-exempt paths are configured in `auth_middleware.py`

## API Testing

### Test Script

Run `test_all_apis.sh` from the API project root to test all endpoints:

```bash
cd smart-invoice-pro-api-2
bash test_all_apis.sh
```

The script automatically generates a fresh JWT token and tests all GET endpoints across all 34 blueprints.

### When to Run Tests

**Always run `test_all_apis.sh` after:**
- Adding, modifying, or removing any API route
- Changing blueprint registration in `app.py`
- Modifying auth middleware or auth-related code
- Changing `cosmos_client.py` container definitions
- Updating any `get_user_from_request()` or similar auth helper functions

### Manual Token Generation

If you need to test individual endpoints manually:

```bash
cd smart-invoice-pro-api-2
source venv/bin/activate
TOKEN=$(python3 -c "
import jwt, time, os
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv('JWT_SECRET_KEY', os.getenv('SECRET_KEY', 'your_secret_key'))
print(jwt.encode({
    'id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'user_id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'tenant_id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'username': 'davinder',
    'exp': int(time.time()) + 7200
}, secret, algorithm='HS256'))
")

# Example: test an endpoint
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:5001/api/invoices" | head -20
```

### Running the Backend Locally

```bash
cd smart-invoice-pro-api-2
source venv/bin/activate
python main.py
# Runs on http://127.0.0.1:5001
```

## API Endpoint Map (34 Blueprints, 56+ GET endpoints)

| Blueprint | Base Path | Key GET Endpoints |
|-----------|-----------|-------------------|
| auth | `/api/auth` | login, register, refresh, logout |
| api_core | `/api` | `/ping` |
| invoices | `/api/invoices` | list, get, next-number, pdf |
| customers | `/api/customers` | list, get, overview |
| products | `/api/products` | list, get, stock-summary, low-stock |
| stock | `/api/stock` | test, get, ledger, recent-adjustments |
| dashboard | `/api/dashboard` | summary, low-stock, monthly-revenue, recent-invoices |
| bank_accounts | `/api/bank-accounts` | list, get |
| profile | `/api/profile` | `/me` |
| quotes | `/api/quotes` | list, get, next-number, pdf |
| recurring_profiles | `/api/recurring-profiles` | list, get |
| sales_orders | `/api/sales-orders` | list, get, next-number, pdf |
| vendors | `/api/vendors` | list, get |
| purchase_orders | `/api/purchase-orders` | list, get, next-number, pdf |
| bills | `/api/bills` | list, get, next-number |
| expenses | `/api/expenses` | list, get, stats/summary |
| reports | `/api/reports` | profit-loss, balance-sheet, aging, ap-aging, cash-flow, sales-summary, gst-tax-summary, payments-received, payments-made |
| payments | `/api/payments` | transactions, status |
| reconciliation | `/api/reconciliation` | transactions, matchable |
| roles | `/api` | my-role, users, approvals/pending |
| settings | `/api/settings` | reminders, organization-profile, gst-config, branding, invoice-preferences, taxes, permissions, roles, users, automation, integrations |
| notifications | `/api/notifications` | list |
| audit_logs | `/api/audit-logs` | list |
| cron | `/api/cron` | check-low-stock, schedule-info |
| gst | `/api/gst` | prefill, validate |

## Unit Testing (pytest)

### Test Infrastructure

- **Framework**: pytest + pytest-cov
- **Shared fixtures**: `tests/conftest.py` — JWT helpers, container mocks, sample data
- **Mock strategy**: All Cosmos DB containers are mocked via `_CONTAINER_PATCHES` in conftest. No test hits a real database.

### Running Unit Tests

```bash
cd smart-invoice-pro-api-2
source venv/bin/activate

# Quick run
python -m pytest tests/ -q --tb=short

# With coverage
python -m pytest tests/ -q --cov=smart_invoice_pro.api --cov-report=term-missing --tb=short

# Single file
python -m pytest tests/test_invoices.py -v
```

### When to Run Unit Tests

**Always run `python -m pytest tests/ -q --tb=short` after:**
- Adding, modifying, or removing any API route
- Changing blueprint registration in `app.py`
- Modifying auth middleware or auth-related code
- Changing `cosmos_client.py` container definitions
- Updating validation logic in any API module

### Writing Tests for New/Modified APIs

When creating or updating an API endpoint, **always create or update the corresponding test file** in `tests/`. Follow these conventions:

#### Test File Structure

```python
# tests/test_<module>.py
import pytest
from unittest.mock import MagicMock, patch
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


class TestCreate<Resource>:
    """POST /<resource> tests."""

    def test_create_success(self, client, headers_a):
        """Happy path — valid payload returns 201 with correct fields."""
        with patch("<module>.container") as mock_ctr:
            mock_ctr.create_item.return_value = {<expected_doc>}
            mock_ctr.query_items.return_value = []  # no duplicates
            resp = client.post("/api/<resource>", json={<valid_payload>}, headers=headers_a)
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["<key_field>"] == "<expected_value>"
            assert data.get("tenant_id") == TENANT_A

    def test_create_missing_required_field(self, client, headers_a):
        """Missing required field returns 400 with error message."""
        resp = client.post("/api/<resource>", json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_create_invalid_field(self, client, headers_a):
        """Invalid field value (e.g. bad email) returns 400."""
        ...

    def test_create_stores_tenant_id(self, client, headers_a):
        """Verify tenant_id from JWT is persisted in the document."""
        with patch("<module>.container") as mock_ctr:
            mock_ctr.create_item.return_value = {}
            ...
            call_args = mock_ctr.create_item.call_args[0][0]
            assert call_args["tenant_id"] == TENANT_A


class TestUpdate<Resource>:
    """PUT /<resource>/<id> tests."""

    def test_update_success(self, client, headers_a):
        """Valid update returns 200 with updated fields."""
        with patch("<module>.container") as mock_ctr:
            mock_ctr.read_item.return_value = {<existing_doc_with_tenant_a>}
            mock_ctr.replace_item.return_value = {<updated_doc>}
            resp = client.put("/api/<resource>/id-1", json={<update_payload>}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        """Non-existent resource returns 404."""
        ...

    def test_update_cross_tenant_forbidden(self, client, headers_b):
        """Tenant B cannot update Tenant A's resource → 403."""
        with patch("<module>.container") as mock_ctr:
            mock_ctr.read_item.return_value = {"tenant_id": TENANT_A}
            resp = client.put("/api/<resource>/id-1", json={...}, headers=headers_b)
            assert resp.status_code == 403

    def test_update_validation(self, client, headers_a):
        """Invalid data in update returns 400."""
        ...
```

#### Key Testing Rules

1. **Always mock containers** — Use `patch("<module>.<container>")` for each test or rely on the `app` fixture global mocks
2. **Test tenant isolation** — If the route checks `tenant_id`, test that Tenant B gets 403 on Tenant A's data
3. **Test all validation** — Required fields (400), invalid values (400), edge cases
4. **Test auth** — Endpoints requiring specific roles should be tested with/without the role
5. **Use shared fixtures** — `client`, `headers_a`, `headers_b`, `sample_*`, `stored_*_a` from conftest
6. **No real DB calls** — Every container must be mocked; tests run fully offline
7. **Verify side effects** — Check `create_item`, `replace_item`, `delete_item` call args for correct tenant_id, timestamps, etc.
8. **Response sanitization** — Verify no `_rid`, `_self`, `_etag`, `_attachments`, `_ts`, `password` in responses

#### Adding Container Mocks for New Modules

When adding a new API module, add its container import path to `_CONTAINER_PATCHES` in `tests/conftest.py`:

```python
_CONTAINER_PATCHES = [
    ...
    "smart_invoice_pro.api.<new_module>.<container_name>",
]
```

### Test Coverage Targets

| Module | Minimum Coverage |
|--------|-----------------|
| Auth middleware | 90%+ |
| CRUD endpoints (invoices, customers, products, etc.) | 60%+ |
| Payment/stock flows | 60%+ |
| Settings/config endpoints | 40%+ |
| Reports (read-only) | 30%+ |

## Common Pitfalls

1. **Double `/api` prefix**: Blueprint routes should NOT include `/api/` — it's already provided by `url_prefix="/api"` in `app.py`
2. **Empty route paths**: Always include the resource name in route decorators (e.g., `/expenses` not `''`)
3. **Container access**: Use imported container variables, not `get_container()` with one arg
4. **Auth headers**: Use `request.user_id` / `request.tenant_id` from JWT middleware, not `X-User-Id` headers
5. **Blueprint names**: Must be unique across the entire app; check existing names before creating new blueprints
6. **Missing tests**: Always create/update tests when adding or modifying API endpoints — CI will fail if tests don't pass
