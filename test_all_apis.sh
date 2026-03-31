#!/bin/bash
# Smart Invoice Pro - Comprehensive API Test Suite
# Usage: ./test_all_apis.sh [TOKEN]

set -e

cd "$(dirname "$0")"
source venv/bin/activate

# Generate fresh token if not provided
if [ -z "$1" ]; then
  echo "Generating fresh JWT token..."
  TOKEN=$(python3 -c "
import jwt, time, os
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv('JWT_SECRET_KEY', os.getenv('SECRET_KEY', 'your_secret_key'))
payload = {
    'id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'user_id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'tenant_id': '4e39d516-2aa0-438a-94aa-f9ca8be4dfe3',
    'username': 'davinder',
    'exp': int(time.time()) + 7200
}
print(jwt.encode(payload, secret, algorithm='HS256'))
")
else
  TOKEN="$1"
fi

B="http://127.0.0.1:5001/api"
PASS=0
FAIL=0
WARN=0
FAILURES=""

test_endpoint() {
  local name="$1" method="$2" url="$3"
  local code
  code=$(curl -s -o /tmp/api_resp.json -w "%{http_code}" -X "$method" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$url" 2>/dev/null)
  local body=$(cat /tmp/api_resp.json 2>/dev/null | head -c 300)
  if [[ "$code" =~ ^2 ]]; then
    echo "  PASS  $name -> $code"
    PASS=$((PASS + 1))
  elif [[ "$code" == "404" ]]; then
    echo "  FAIL  $name -> $code NOT FOUND"
    FAIL=$((FAIL + 1))
    FAILURES="$FAILURES\n  $name -> $code | $body"
  elif [[ "$code" == "500" ]]; then
    echo "  FAIL  $name -> $code SERVER ERROR | $body"
    FAIL=$((FAIL + 1))
    FAILURES="$FAILURES\n  $name -> $code | $body"
  else
    echo "  WARN  $name -> $code | $body"
    WARN=$((WARN + 1))
    FAILURES="$FAILURES\n  $name -> $code | $body"
  fi
}

echo "========================================="
echo "  SMART INVOICE PRO - API TEST SUITE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

# Health check
echo "--- HEALTH CHECK ---"
HC=$(curl -s -o /dev/null -w "%{http_code}" "$B/../" 2>/dev/null)
if [[ "$HC" == "200" ]]; then
  echo "  PASS  Backend is running"
else
  echo "  FAIL  Backend not reachable (HTTP $HC)"
  exit 1
fi
echo ""

echo "--- PING ---"
test_endpoint "GET /ping" GET "$B/ping"
echo ""

echo "--- PROFILE ---"
test_endpoint "GET /profile/me" GET "$B/profile/me"
echo ""

echo "--- DASHBOARD ---"
test_endpoint "GET /dashboard/summary" GET "$B/dashboard/summary"
test_endpoint "GET /dashboard/low-stock" GET "$B/dashboard/low-stock"
test_endpoint "GET /dashboard/monthly-revenue" GET "$B/dashboard/monthly-revenue"
test_endpoint "GET /dashboard/recent-invoices" GET "$B/dashboard/recent-invoices"
echo ""

echo "--- CUSTOMERS ---"
test_endpoint "GET /customers" GET "$B/customers"
echo ""

echo "--- PRODUCTS ---"
test_endpoint "GET /products" GET "$B/products"
test_endpoint "GET /products/stock-summary" GET "$B/products/stock-summary"
test_endpoint "GET /products/low-stock" GET "$B/products/low-stock"
echo ""

echo "--- INVOICES ---"
test_endpoint "GET /invoices" GET "$B/invoices"
test_endpoint "GET /invoices/next-number" GET "$B/invoices/next-number"
echo ""

echo "--- QUOTES ---"
test_endpoint "GET /quotes" GET "$B/quotes"
test_endpoint "GET /quotes/next-number" GET "$B/quotes/next-number"
echo ""

echo "--- SALES ORDERS ---"
test_endpoint "GET /sales-orders" GET "$B/sales-orders"
test_endpoint "GET /sales-orders/next-number" GET "$B/sales-orders/next-number"
echo ""

echo "--- VENDORS ---"
test_endpoint "GET /vendors" GET "$B/vendors"
echo ""

echo "--- PURCHASE ORDERS ---"
test_endpoint "GET /purchase-orders" GET "$B/purchase-orders"
test_endpoint "GET /purchase-orders/next-number" GET "$B/purchase-orders/next-number"
echo ""

echo "--- BILLS ---"
test_endpoint "GET /bills" GET "$B/bills"
test_endpoint "GET /bills/next-number" GET "$B/bills/next-number"
echo ""

echo "--- EXPENSES ---"
test_endpoint "GET /expenses" GET "$B/expenses"
test_endpoint "GET /expenses/stats/summary" GET "$B/expenses/stats/summary"
echo ""

echo "--- BANK ACCOUNTS ---"
test_endpoint "GET /bank-accounts" GET "$B/bank-accounts"
echo ""

echo "--- STOCK ---"
test_endpoint "GET /stock/test" GET "$B/stock/test"
test_endpoint "GET /stock/recent-adjustments" GET "$B/stock/recent-adjustments"
echo ""

echo "--- SETTINGS ---"
test_endpoint "GET /settings/reminders" GET "$B/settings/reminders"
test_endpoint "GET /settings/organization-profile" GET "$B/settings/organization-profile"
test_endpoint "GET /settings/gst-config" GET "$B/settings/gst-config"
test_endpoint "GET /settings/branding" GET "$B/settings/branding"
test_endpoint "GET /settings/invoice-preferences" GET "$B/settings/invoice-preferences"
test_endpoint "GET /settings/taxes" GET "$B/settings/taxes"
test_endpoint "GET /settings/permissions" GET "$B/settings/permissions"
test_endpoint "GET /settings/roles" GET "$B/settings/roles"
test_endpoint "GET /settings/users" GET "$B/settings/users"
test_endpoint "GET /settings/automation" GET "$B/settings/automation"
test_endpoint "GET /settings/integrations" GET "$B/settings/integrations"
echo ""

echo "--- NOTIFICATIONS ---"
test_endpoint "GET /notifications" GET "$B/notifications"
echo ""

echo "--- AUDIT LOGS ---"
test_endpoint "GET /audit-logs" GET "$B/audit-logs"
echo ""

echo "--- ROLES & APPROVALS ---"
test_endpoint "GET /my-role" GET "$B/my-role"
test_endpoint "GET /users" GET "$B/users"
test_endpoint "GET /approvals/pending" GET "$B/approvals/pending"
echo ""

echo "--- BANK RECONCILIATION ---"
test_endpoint "GET /reconciliation/transactions" GET "$B/reconciliation/transactions"
test_endpoint "GET /reconciliation/matchable" GET "$B/reconciliation/matchable"
echo ""

echo "--- CRON ---"
test_endpoint "GET /cron/check-low-stock" GET "$B/cron/check-low-stock"
test_endpoint "GET /cron/schedule-info" GET "$B/cron/schedule-info"
echo ""

echo "--- PAYMENTS ---"
test_endpoint "GET /payments/transactions" GET "$B/payments/transactions?user_id=4e39d516-2aa0-438a-94aa-f9ca8be4dfe3"
echo ""

echo "--- REPORTS ---"
test_endpoint "GET /reports/profit-loss" GET "$B/reports/profit-loss?start_date=2025-01-01&end_date=2026-03-31"
test_endpoint "GET /reports/balance-sheet" GET "$B/reports/balance-sheet?as_of_date=2026-03-31"
test_endpoint "GET /reports/aging" GET "$B/reports/aging?as_of_date=2026-03-31"
test_endpoint "GET /reports/ap-aging" GET "$B/reports/ap-aging?as_of_date=2026-03-31"
test_endpoint "GET /reports/cash-flow" GET "$B/reports/cash-flow?start_date=2025-01-01&end_date=2026-03-31"
test_endpoint "GET /reports/sales-summary" GET "$B/reports/sales-summary?start_date=2025-01-01&end_date=2026-03-31"
test_endpoint "GET /reports/gst-tax-summary" GET "$B/reports/gst-tax-summary?start_date=2025-01-01&end_date=2026-03-31"
test_endpoint "GET /reports/payments-received" GET "$B/reports/payments-received?start_date=2025-01-01&end_date=2026-03-31"
test_endpoint "GET /reports/payments-made" GET "$B/reports/payments-made?start_date=2025-01-01&end_date=2026-03-31"
echo ""

echo "========================================="
echo "  RESULTS"
echo "========================================="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  WARN: $WARN"
echo "  TOTAL: $((PASS + FAIL + WARN))"
if [ $FAIL -gt 0 ] || [ $WARN -gt 0 ]; then
  echo ""
  echo "  ISSUES:"
  echo -e "$FAILURES"
fi
echo "========================================="
