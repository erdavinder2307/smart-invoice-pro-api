"""
Tests for paid plans through Razorpay Subscriptions (api/billing_api.py): the public plan list,
starting and cancelling a subscription (Razorpay HTTP mocked), and the signed webhook.
Every key, secret and plan id here is a dummy value.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from azure.cosmos import exceptions

from tests.conftest import TENANT_A, TENANT_B, USER_A, auth_headers

WEBHOOK_SECRET = "test-webhook-secret"
BILLING_ENV = {
    "BILLING_ENABLED": "true",
    "RAZORPAY_KEY_ID": "rzp_test_dummy",
    "RAZORPAY_KEY_SECRET": "dummy-key-secret",
    "RAZORPAY_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "RAZORPAY_PLAN_STARTER_MONTHLY": "plan_starter_m",
    "RAZORPAY_PLAN_STARTER_YEARLY": "plan_starter_y",
    "RAZORPAY_PLAN_GROWTH_MONTHLY": "plan_growth_m",
    "SELLER_LEGAL_NAME": "Example Seller Private Limited",
    "SELLER_CIN": "U00000PB2000PTC000000",
    "SELLER_GSTIN": "03AAAAA0000A1Z5",
    "SELLER_ADDRESS_LINES": "1 Test Street|Mohali, Punjab 140000",
    "BILLING_SAC_CODE": "997331",
}
PERIOD_END = 1793836800  # 2026-11-05T00:00:00 UTC


@pytest.fixture()
def billing_env(monkeypatch):
    for name, value in BILLING_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture()
def mocks(app):
    """The billing module's containers (mocked in conftest) plus an Admin user."""
    from smart_invoice_pro.api import billing_api, roles_api
    roles_api.users_container.query_items.return_value = [
        {"id": USER_A, "tenant_id": TENANT_A, "role": "Admin", "email": "owner@example.com", "username": "owner"}
    ]
    return billing_api


def _tenant(**fields):
    base = {"id": TENANT_A, "name": "Acme", "plan": "trial", "status": "active", "tenant_type": "PRODUCTION",
            "trial_ends_at": "2026-11-01T00:00:00"}
    base.update(fields)
    return base


def _razorpay_response(body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    return resp


# ── Plans ─────────────────────────────────────────────────────────────────────
class TestPlans:
    def test_plans_are_public_and_show_gst(self, client, mocks):
        resp = client.get("/api/billing/plans")
        assert resp.status_code == 200
        body = resp.get_json()
        starter = next(p for p in body["plans"] if p["code"] == "starter")
        assert starter["prices"]["monthly"] == {"amount": 599, "amount_with_gst": 707}
        assert starter["prices"]["yearly"] == {"amount": 5999, "amount_with_gst": 7079}
        growth = next(p for p in body["plans"] if p["code"] == "growth")
        assert growth["prices"] == {"monthly": {"amount": 1499, "amount_with_gst": 1769}}
        assert growth["seats"] == 10

    def test_checkout_is_off_by_default(self, client, mocks, monkeypatch):
        monkeypatch.delenv("BILLING_ENABLED", raising=False)
        assert client.get("/api/billing/plans").get_json()["checkout_enabled"] is False

    def test_checkout_is_on_with_config(self, client, mocks, billing_env):
        assert client.get("/api/billing/plans").get_json()["checkout_enabled"] is True

    def test_checkout_stays_off_without_tax_invoice_details(self, client, mocks, billing_env, monkeypatch):
        monkeypatch.delenv("SELLER_GSTIN")
        assert client.get("/api/billing/plans").get_json()["checkout_enabled"] is False


# ── Start a subscription ──────────────────────────────────────────────────────
class TestCreateSubscription:
    def _post(self, client, body, tenant=None):
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id", return_value=tenant or _tenant()):
            return client.post("/api/billing/subscriptions", json=body, headers=auth_headers())

    def test_disabled_returns_503(self, client, mocks, monkeypatch):
        monkeypatch.delenv("BILLING_ENABLED", raising=False)
        assert self._post(client, {"plan_code": "starter", "period": "monthly"}).status_code == 503

    def test_needs_sign_in(self, client, mocks, billing_env):
        resp = client.post("/api/billing/subscriptions", json={"plan_code": "starter", "period": "monthly"})
        assert resp.status_code == 401

    def test_only_admin(self, client, mocks, billing_env):
        from smart_invoice_pro.api import roles_api
        roles_api.users_container.query_items.return_value = [{"id": USER_A, "role": "Sales"}]
        assert self._post(client, {"plan_code": "starter", "period": "monthly"}).status_code == 403

    @pytest.mark.parametrize("body", [
        {"plan_code": "gold", "period": "monthly"},
        {"plan_code": "starter", "period": "weekly"},
        {"plan_code": "growth", "period": "yearly"},  # no Growth yearly price yet
        {},
    ])
    def test_unknown_plan_or_period_is_400(self, client, mocks, billing_env, body):
        with patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            assert self._post(client, body).status_code == 400
        post.assert_not_called()

    def test_already_active_is_409(self, client, mocks, billing_env):
        tenant = _tenant(plan="starter", billing={"status": "active", "subscription_id": "sub_1"})
        assert self._post(client, {"plan_code": "growth", "period": "monthly"}, tenant).status_code == 409

    def test_creates_customer_and_subscription(self, client, mocks, billing_env):
        tenant = _tenant()
        with patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            post.side_effect = [
                _razorpay_response({"id": "cust_1"}),
                _razorpay_response({"id": "sub_1", "status": "created"}),
            ]
            resp = self._post(client, {"plan_code": "starter", "period": "yearly"}, tenant)

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["subscription_id"] == "sub_1"
        assert body["key_id"] == "rzp_test_dummy"
        assert "dummy-key-secret" not in resp.get_data(as_text=True)

        customer_call, sub_call = post.call_args_list
        assert customer_call.args[0].endswith("/customers")
        assert customer_call.kwargs["json"]["email"] == "owner@example.com"
        assert sub_call.args[0].endswith("/subscriptions")
        assert sub_call.kwargs["json"]["plan_id"] == "plan_starter_y"
        assert sub_call.kwargs["json"]["total_count"] == 1
        assert sub_call.kwargs["json"]["customer_id"] == "cust_1"
        assert sub_call.kwargs["json"]["notes"]["tenant_id"] == TENANT_A
        assert sub_call.kwargs["auth"] == ("rzp_test_dummy", "dummy-key-secret")

        # Customer id kept on the tenant; the plan itself only changes through the webhook.
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        assert saved["billing"]["customer_id"] == "cust_1"
        assert saved["plan"] == "trial"
        stored = mocks.subscriptions_container.upsert_item.call_args.kwargs["body"]
        assert stored["id"] == "sub_1" and stored["tenant_id"] == TENANT_A

    def test_reuses_existing_customer(self, client, mocks, billing_env):
        tenant = _tenant(billing={"customer_id": "cust_9", "status": "cancelled"})
        with patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            post.return_value = _razorpay_response({"id": "sub_2", "status": "created"})
            resp = self._post(client, {"plan_code": "growth", "period": "monthly"}, tenant)
        assert resp.status_code == 201
        assert post.call_count == 1
        assert post.call_args.kwargs["json"]["customer_id"] == "cust_9"
        assert post.call_args.kwargs["json"]["total_count"] == 12

    def test_razorpay_error_is_502(self, client, mocks, billing_env):
        with patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            post.return_value = _razorpay_response({"error": {}}, status=400)
            resp = self._post(client, {"plan_code": "starter", "period": "monthly"})
        assert resp.status_code == 502
        mocks.subscriptions_container.upsert_item.assert_not_called()

    def test_allowed_after_trial_end(self, client, mocks, billing_env):
        # A tenant whose trial ended must still be able to pay.
        expired = _tenant(trial_ends_at="2020-01-01T00:00:00")
        with patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=expired), \
                patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            post.side_effect = [_razorpay_response({"id": "cust_1"}), _razorpay_response({"id": "sub_1"})]
            resp = self._post(client, {"plan_code": "starter", "period": "monthly"}, expired)
        assert resp.status_code == 201


# ── Status and cancel ─────────────────────────────────────────────────────────
class TestSubscriptionStatus:
    def test_status(self, client, mocks):
        tenant = _tenant(plan="starter", plan_period_end="2026-11-05T00:00:00",
                         billing={"status": "active", "plan_code": "starter", "period": "monthly",
                                  "subscription_id": "sub_1", "customer_id": "cust_1"})
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id", return_value=tenant):
            resp = client.get("/api/billing/subscription", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["plan"] == "starter"
        assert body["plan_period_end"] == "2026-11-05T00:00:00"
        assert body["billing"]["status"] == "active"
        assert "customer_id" not in body["billing"]

    def test_cancel_at_period_end(self, client, mocks, billing_env):
        tenant = _tenant(plan="starter", billing={"status": "active", "subscription_id": "sub_1"})
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id", return_value=tenant), \
                patch("smart_invoice_pro.api.billing_api.requests.post") as post:
            post.return_value = _razorpay_response({"id": "sub_1", "status": "active"})
            resp = client.post("/api/billing/subscriptions/cancel", headers=auth_headers())
        assert resp.status_code == 200
        assert post.call_args.args[0].endswith("/subscriptions/sub_1/cancel")
        assert post.call_args.kwargs["json"] == {"cancel_at_cycle_end": 1}
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        assert saved["billing"]["cancel_at_period_end"] is True
        assert saved["plan"] == "starter"

    def test_cancel_without_subscription_is_409(self, client, mocks, billing_env):
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id", return_value=_tenant()):
            resp = client.post("/api/billing/subscriptions/cancel", headers=auth_headers())
        assert resp.status_code == 409


# ── Webhook ───────────────────────────────────────────────────────────────────
def _event(event, plan_id="plan_starter_m", sub_id="sub_1", tenant_id=TENANT_A, status="active"):
    return {
        "entity": "event",
        "event": event,
        "payload": {"subscription": {"entity": {
            "id": sub_id,
            "plan_id": plan_id,
            "customer_id": "cust_1",
            "status": status,
            "current_end": PERIOD_END,
            "notes": {"tenant_id": tenant_id},
        }}},
    }


def _send(client, payload, event_id="evt_1", secret=WEBHOOK_SECRET, signature=None):
    raw = json.dumps(payload).encode()
    sig = signature if signature is not None else hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/billing/webhook",
        data=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id},
    )


class TestWebhook:
    def _send_for(self, client, tenant, payload, **kw):
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id", return_value=tenant):
            return _send(client, payload, **kw)

    def test_no_login_needed(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated"))
        assert resp.status_code == 200

    def test_activated_sets_plan_and_period_end(self, client, mocks, billing_env):
        tenant = _tenant(billing={"customer_id": "cust_1", "provider": "razorpay"})
        resp = self._send_for(client, tenant, _event("subscription.activated"))
        assert resp.status_code == 200
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        assert saved["plan"] == "starter"
        assert saved["status"] == "active"
        assert saved["plan_period_end"] == "2026-11-05T00:00:00"
        assert saved["billing"] == {"customer_id": "cust_1", "provider": "razorpay", "subscription_id": "sub_1",
                                    "plan_code": "starter", "period": "monthly", "status": "active"}

    def test_charged_moves_period_end(self, client, mocks, billing_env):
        tenant = _tenant(plan="growth", plan_period_end="2026-10-05T00:00:00",
                         billing={"status": "active", "subscription_id": "sub_1"})
        resp = self._send_for(client, tenant, _event("subscription.charged", plan_id="plan_growth_m"))
        assert resp.status_code == 200
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        assert saved["plan"] == "growth"
        assert saved["plan_period_end"] == "2026-11-05T00:00:00"

    def test_bad_signature_is_400_and_changes_nothing(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), signature="0" * 64)
        assert resp.status_code == 400
        mocks.tenants_container.replace_item.assert_not_called()
        mocks.billing_events_container.create_item.assert_not_called()

    def test_signed_with_other_secret_is_400(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), secret="someone-else")
        assert resp.status_code == 400

    def test_missing_signature_is_400(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), signature="")
        assert resp.status_code == 400

    def test_secret_unset_is_503_and_never_skips_the_check(self, client, mocks, billing_env, monkeypatch):
        monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), signature="")
        assert resp.status_code == 503
        mocks.tenants_container.replace_item.assert_not_called()

    def test_replayed_event_is_not_applied_twice(self, client, mocks, billing_env):
        mocks.billing_events_container.create_item.side_effect = exceptions.CosmosResourceExistsError(
            status_code=409, message="exists")
        resp = self._send_for(client, _tenant(), _event("subscription.activated"))
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "already processed"
        mocks.tenants_container.replace_item.assert_not_called()

    def test_event_id_is_recorded(self, client, mocks, billing_env):
        self._send_for(client, _tenant(), _event("subscription.activated"), event_id="evt_42")
        body = mocks.billing_events_container.create_item.call_args.kwargs["body"]
        assert body["id"] == "evt_42" and body["event_id"] == "evt_42"

    def test_missing_event_id_is_400(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), event_id="")
        assert resp.status_code == 400

    def test_unknown_plan_is_200_and_changes_nothing(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("subscription.activated", plan_id="plan_other"))
        assert resp.status_code == 200
        mocks.tenants_container.replace_item.assert_not_called()

    def test_unknown_event_is_200_and_changes_nothing(self, client, mocks, billing_env):
        resp = self._send_for(client, _tenant(), _event("payment.captured"))
        assert resp.status_code == 200
        mocks.tenants_container.replace_item.assert_not_called()

    def test_unknown_tenant_is_200_and_changes_nothing(self, client, mocks, billing_env):
        resp = self._send_for(client, None, _event("subscription.activated"))
        assert resp.status_code == 200
        mocks.tenants_container.replace_item.assert_not_called()

    def test_stored_subscription_tenant_wins_over_notes(self, client, mocks, billing_env):
        mocks.subscriptions_container.query_items.return_value = [{"id": "sub_1", "tenant_id": TENANT_B}]
        with patch("smart_invoice_pro.api.billing_api.get_tenant_by_id") as get_tenant:
            get_tenant.return_value = _tenant(id=TENANT_B)
            _send(client, _event("subscription.activated", tenant_id=TENANT_A))
        get_tenant.assert_called_with(TENANT_B)

    @pytest.mark.parametrize("event,status", [
        ("subscription.halted", "halted"),
        ("subscription.pending", "pending"),
        ("subscription.cancelled", "cancelled"),
        ("subscription.completed", "cancelled"),
    ])
    def test_status_events_keep_plan_and_period(self, client, mocks, billing_env, event, status):
        tenant = _tenant(plan="starter", plan_period_end="2026-11-05T00:00:00",
                         billing={"status": "active", "subscription_id": "sub_1"})
        resp = self._send_for(client, tenant, _event(event, status=status))
        assert resp.status_code == 200
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        assert saved["billing"]["status"] == status
        assert saved["plan"] == "starter"
        assert saved["plan_period_end"] == "2026-11-05T00:00:00"

    def test_status_event_for_an_old_subscription_is_ignored(self, client, mocks, billing_env):
        tenant = _tenant(plan="growth", billing={"status": "active", "subscription_id": "sub_new"})
        resp = self._send_for(client, tenant, _event("subscription.cancelled", sub_id="sub_old"))
        assert resp.status_code == 200
        mocks.tenants_container.replace_item.assert_not_called()

    def test_storage_error_releases_event_for_retry(self, client, mocks, billing_env):
        mocks.tenants_container.replace_item.side_effect = exceptions.CosmosHttpResponseError(
            status_code=503, message="busy")
        resp = self._send_for(client, _tenant(), _event("subscription.activated"), event_id="evt_9")
        assert resp.status_code == 500
        mocks.billing_events_container.delete_item.assert_called_once_with(item="evt_9", partition_key="evt_9")

    def test_paid_tenant_can_write_after_trial_end(self, client, mocks, billing_env):
        # After activation the tenant is on starter, so the trial-end lock no longer applies.
        self._send_for(client, _tenant(trial_ends_at="2020-01-01T00:00:00"), _event("subscription.activated"))
        saved = mocks.tenants_container.replace_item.call_args.kwargs["body"]
        from datetime import datetime
        from smart_invoice_pro.utils.entitlements import account_lock_code
        assert account_lock_code(saved, datetime(2026, 10, 20)) is None
