"""
Integration tests for subscription and plans API endpoints.

Uses the shared `app` and `client` fixtures from conftest.py.
All Cosmos containers are mocked via conftest; individual tests add extra
mocks where they need fine-grained control over return values.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import TENANT_A, USER_A, auth_headers


# ── Helpers ────────────────────────────────────────────────────────────────────

def _headers():
    return auth_headers(user_id=USER_A, tenant_id=TENANT_A)


def _make_sub(status="trial"):
    now = datetime.now(timezone.utc)
    return {
        "id": f"sub_{TENANT_A}",
        "tenant_id": TENANT_A,
        "plan_id": "starter_monthly",
        "status": status,
        "billing_cycle": "monthly",
        "trial_ends_at": (now + timedelta(days=10)).isoformat(),
        "current_period_end": (now + timedelta(days=10)).isoformat(),
        "trial_days_granted": 30,
        "trial_extended_by_days": 0,
        "trial_extension_1_requested_at": None,
        "trial_extension_2_requested_at": None,
        "is_founding_customer": False,
        "auto_renew": False,
        "amount_inr": None,
        "seats": 3,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _make_plan():
    return {
        "id": "starter_monthly",
        "name": "Starter",
        "slug": "starter",
        "pricing": {"inr_monthly": 599, "inr_annual": 5999},
        "features": {
            "invoices_per_month": 100,
            "max_users": 3,
            "api_access": False,
            "advanced_reports": False,
            "automation_rules": 0,
        },
        "is_public": True,
        "is_active": True,
        "sort_order": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/plans  (no auth required)
# ─────────────────────────────────────────────────────────────────────────────

class TestPlansEndpoint:

    def test_plans_accessible_without_token(self, client):
        with patch("smart_invoice_pro.services.subscription_service.plans_container") as mp:
            mp.query_items.return_value = [_make_plan()]
            resp = client.get("/api/plans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "plans" in data
        assert data["count"] >= 0

    def test_plans_returns_list(self, client):
        plans = [_make_plan(), {**_make_plan(), "id": "starter_annual", "name": "Starter Annual"}]
        with patch("smart_invoice_pro.services.subscription_service.plans_container") as mp:
            mp.query_items.return_value = plans
            resp = client.get("/api/plans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2

    def test_plans_returns_empty_on_no_plans(self, client):
        with patch("smart_invoice_pro.services.subscription_service.plans_container") as mp:
            mp.query_items.return_value = []
            resp = client.get("/api/plans")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/subscription/current
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCurrentSubscription:

    def test_returns_subscription_for_authenticated_user(self, client):
        sub = _make_sub()
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms, \
             patch("smart_invoice_pro.services.subscription_service.plans_container") as mp, \
             patch("smart_invoice_pro.services.subscription_service.usage_records_container") as mu:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [_make_plan()]
            mu.query_items.return_value = [{"metrics": {}}]
            resp = client.get("/api/subscription/current", headers=_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subscription"]["status"] == "trial"
        assert "trial_days_remaining" in data
        assert "entitlements" in data

    def test_returns_404_when_no_subscription(self, client):
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms:
            ms.query_items.return_value = []
            resp = client.get("/api/subscription/current", headers=_headers())
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.get("/api/subscription/current")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/subscription/usage
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSubscriptionUsage:

    def test_returns_usage_metrics(self, client):
        sub = _make_sub()
        usage = {
            "id": f"usage_{TENANT_A}_2026_06",
            "tenant_id": TENANT_A,
            "billing_period_start": "2026-06-01T00:00:00+00:00",
            "billing_period_end": "2026-07-01T00:00:00+00:00",
            "metrics": {"invoices_created": 5, "users_active": 1},
        }
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms, \
             patch("smart_invoice_pro.services.subscription_service.plans_container") as mp, \
             patch("smart_invoice_pro.services.subscription_service.usage_records_container") as mu:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [_make_plan()]
            mu.query_items.return_value = [usage]
            resp = client.get("/api/subscription/usage", headers=_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["usage"]["invoices_created"] == 5
        assert "limits" in data

    def test_requires_auth(self, client):
        resp = client.get("/api/subscription/usage")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/subscription/request-extension
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestExtensionEndpoint:

    def test_extension_1_returns_granted(self, client):
        sub = _make_sub()
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms, \
             patch("smart_invoice_pro.services.subscription_service.subscription_events_container"):
            ms.query_items.return_value = [sub]
            resp = client.post(
                "/api/subscription/request-extension",
                json={"extension_type": "1", "reason": ""},
                headers=_headers(),
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "granted"

    def test_missing_extension_type_returns_400(self, client):
        resp = client.post(
            "/api/subscription/request-extension",
            json={},
            headers=_headers(),
        )
        assert resp.status_code == 400
        assert "extension_type" in resp.get_json().get("error", "")

    def test_invalid_extension_type_returns_400(self, client):
        resp = client.post(
            "/api/subscription/request-extension",
            json={"extension_type": "9"},
            headers=_headers(),
        )
        assert resp.status_code == 400

    def test_requires_auth(self, client):
        resp = client.post("/api/subscription/request-extension", json={"extension_type": "1"})
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/subscription/cancel
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelEndpoint:

    def test_cancel_returns_cancelled_status(self, client):
        sub = _make_sub(status="active")
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms, \
             patch("smart_invoice_pro.services.subscription_service.subscription_events_container"):
            ms.query_items.return_value = [sub]
            resp = client.post(
                "/api/subscription/cancel",
                json={"reason": "too expensive"},
                headers=_headers(),
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "cancelled"
        assert data["cancelled_at"] is not None

    def test_cancel_with_no_subscription_returns_400(self, client):
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms:
            ms.query_items.return_value = []
            resp = client.post(
                "/api/subscription/cancel",
                json={"reason": ""},
                headers=_headers(),
            )
        assert resp.status_code == 400

    def test_requires_auth(self, client):
        resp = client.post("/api/subscription/cancel", json={"reason": ""})
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/subscription/extension-requests
# ─────────────────────────────────────────────────────────────────────────────

class TestListExtensionRequests:

    def test_returns_requests_list(self, client):
        requests_data = [
            {"id": "req-001", "tenant_id": TENANT_A, "status": "pending", "extension_type": "2"},
        ]
        with patch("smart_invoice_pro.api.subscription_api.trial_extension_requests_container") as mc:
            mc.query_items.return_value = requests_data
            resp = client.get("/api/subscription/extension-requests", headers=_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["requests"]) == 1
        assert data["requests"][0]["status"] == "pending"

    def test_returns_empty_list_on_no_requests(self, client):
        with patch("smart_invoice_pro.api.subscription_api.trial_extension_requests_container") as mc:
            mc.query_items.return_value = []
            resp = client.get("/api/subscription/extension-requests", headers=_headers())
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_requires_auth(self, client):
        resp = client.get("/api/subscription/extension-requests")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/billing/webhook  (no auth required)
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingWebhook:

    def _webhook_payload(self, tenant_id=TENANT_A, plan_id="starter_monthly",
                         billing_cycle="monthly", amount=599.0):
        return {
            "event_type": "payment.success",
            "data": {
                "reference_id": f"sub_{tenant_id}_{billing_cycle}_{plan_id}",
                "payment_link_id": "zpl_abc123",
                "amount": amount,
                "custom_fields": [
                    {"label": "Tenant ID", "value": tenant_id},
                    {"label": "Plan ID", "value": plan_id},
                    {"label": "Billing Cycle", "value": billing_cycle},
                ],
            },
        }

    def test_payment_success_activates_subscription(self, client):
        sub = {"id": f"sub_{TENANT_A}", "tenant_id": TENANT_A, "status": "trial",
               "trial_ends_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
               "created_at": datetime.now(timezone.utc).isoformat()}
        plan = _make_plan()
        with patch("smart_invoice_pro.services.subscription_service.subscriptions_container") as ms, \
             patch("smart_invoice_pro.services.subscription_service.plans_container") as mp, \
             patch("smart_invoice_pro.services.subscription_service.tenants_container") as mt, \
             patch("smart_invoice_pro.services.subscription_service.subscription_events_container"), \
             patch("smart_invoice_pro.api.billing_api.billing_invoices_container") as mbi:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [plan]
            mt.query_items.return_value = []
            mbi.query_items.return_value = []
            resp = client.post(
                "/api/billing/webhook",
                json=self._webhook_payload(),
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        # verify activate was called (replace_item on the sub container)
        ms.replace_item.assert_called()

    def test_ignores_unknown_event_types(self, client):
        payload = {"event_type": "payment.refunded", "data": {}}
        resp = client.post("/api/billing/webhook", json=payload, content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ignored"

    def test_no_auth_required(self, client):
        """Webhook must be callable without a JWT — it's a Zoho callback."""
        payload = {"event_type": "payment.refunded", "data": {}}
        resp = client.post("/api/billing/webhook", json=payload, content_type="application/json")
        assert resp.status_code != 401

    def test_invalid_signature_returns_401_when_secret_set(self, client, monkeypatch):
        import os
        monkeypatch.setenv("ZOHO_PAYMENTS_WEBHOOK_SECRET", "super-secret")
        payload = self._webhook_payload()
        resp = client.post(
            "/api/billing/webhook",
            json=payload,
            content_type="application/json",
            headers={"X-Zoho-Payments-Signature": "bad_sig"},
        )
        assert resp.status_code == 401
        monkeypatch.delenv("ZOHO_PAYMENTS_WEBHOOK_SECRET", raising=False)


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/billing/invoices
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingInvoices:

    def test_returns_invoices_list(self, client):
        invoices = [
            {"id": "inv-001", "tenant_id": TENANT_A, "status": "paid",
             "total_inr": 599.0, "issued_at": "2026-06-01T00:00:00+00:00"},
        ]
        with patch("smart_invoice_pro.api.billing_api.billing_invoices_container") as mc:
            mc.query_items.return_value = invoices
            resp = client.get("/api/billing/invoices", headers=_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1
        assert data["invoices"][0]["status"] == "paid"

    def test_returns_empty_list_when_no_invoices(self, client):
        with patch("smart_invoice_pro.api.billing_api.billing_invoices_container") as mc:
            mc.query_items.return_value = []
            resp = client.get("/api/billing/invoices", headers=_headers())
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_requires_auth(self, client):
        resp = client.get("/api/billing/invoices")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
#  GET /api/billing/invoices/<id>
# ─────────────────────────────────────────────────────────────────────────────

class TestGetBillingInvoice:

    def test_returns_single_invoice(self, client):
        invoice = {"id": "inv-001", "tenant_id": TENANT_A, "total_inr": 599.0}
        with patch("smart_invoice_pro.api.billing_api.billing_invoices_container") as mc:
            mc.query_items.return_value = [invoice]
            resp = client.get("/api/billing/invoices/inv-001", headers=_headers())
        assert resp.status_code == 200
        assert resp.get_json()["id"] == "inv-001"

    def test_returns_404_for_missing_invoice(self, client):
        with patch("smart_invoice_pro.api.billing_api.billing_invoices_container") as mc:
            mc.query_items.return_value = []
            resp = client.get("/api/billing/invoices/nonexistent", headers=_headers())
        assert resp.status_code == 404
