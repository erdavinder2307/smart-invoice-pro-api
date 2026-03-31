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

## Common Pitfalls

1. **Double `/api` prefix**: Blueprint routes should NOT include `/api/` — it's already provided by `url_prefix="/api"` in `app.py`
2. **Empty route paths**: Always include the resource name in route decorators (e.g., `/expenses` not `''`)
3. **Container access**: Use imported container variables, not `get_container()` with one arg
4. **Auth headers**: Use `request.user_id` / `request.tenant_id` from JWT middleware, not `X-User-Id` headers
5. **Blueprint names**: Must be unique across the entire app; check existing names before creating new blueprints
