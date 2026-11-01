"""
Unit tests for smart_invoice_pro.services.subscription_service.

All Cosmos containers are mocked — no real database or network calls.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import TENANT_A, TENANT_B

# ── Container mock targets ────────────────────────────────────────────────────
_BASE = "smart_invoice_pro.services.subscription_service"
SUBS_MOD  = f"{_BASE}.subscriptions_container"
EVENTS_MOD = f"{_BASE}.subscription_events_container"
USAGE_MOD  = f"{_BASE}.usage_records_container"
PLANS_MOD  = f"{_BASE}.plans_container"
TENANTS_MOD = f"{_BASE}.tenants_container"
EXT_MOD    = f"{_BASE}.trial_extension_requests_container"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_sub(status="trial", tenant_id=TENANT_A, plan_id="starter_monthly",
              trial_ends_at=None, trial_extension_1_requested_at=None,
              trial_extension_2_requested_at=None, trial_extended_by_days=0,
              created_at=None):
    now = datetime.now(timezone.utc)
    if trial_ends_at is None:
        trial_ends_at = (now + timedelta(days=10)).isoformat()
    if created_at is None:
        created_at = (now - timedelta(days=5)).isoformat()
    return {
        "id": f"sub_{tenant_id}",
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "status": status,
        "billing_cycle": "monthly",
        "trial_ends_at": trial_ends_at,
        "current_period_end": trial_ends_at,
        "trial_days_granted": 30,
        "trial_extended_by_days": trial_extended_by_days,
        "trial_extension_1_requested_at": trial_extension_1_requested_at,
        "trial_extension_2_requested_at": trial_extension_2_requested_at,
        "trial_extension_2_approved_by": None,
        "trial_extension_2_approved_at": None,
        "is_founding_customer": False,
        "cancelled_at": None,
        "cancel_reason": None,
        "payment_gateway": None,
        "gateway_subscription_id": None,
        "auto_renew": False,
        "next_billing_date": None,
        "amount_inr": None,
        "seats": 3,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _make_plan(plan_id="starter_monthly"):
    return {
        "id": plan_id,
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
#  get_subscription
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSubscription:

    def test_returns_subscription_when_found(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as mock_c:
            mock_c.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import get_subscription
            result = get_subscription(TENANT_A)
        assert result["tenant_id"] == TENANT_A
        assert result["status"] == "trial"

    def test_returns_none_when_not_found(self):
        with patch(SUBS_MOD) as mock_c:
            mock_c.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import get_subscription
            result = get_subscription(TENANT_A)
        assert result is None

    def test_returns_none_on_exception(self):
        with patch(SUBS_MOD) as mock_c:
            mock_c.query_items.side_effect = Exception("Cosmos down")
            from smart_invoice_pro.services.subscription_service import get_subscription
            result = get_subscription(TENANT_A)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
#  create_trial_subscription
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateTrialSubscription:

    def test_creates_subscription_with_correct_fields(self):
        with patch(SUBS_MOD) as mock_subs, \
             patch(EVENTS_MOD), \
             patch(TENANTS_MOD) as mock_tenants:
            mock_tenants.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import create_trial_subscription
            result = create_trial_subscription(TENANT_A)

        mock_subs.create_item.assert_called_once()
        body = mock_subs.create_item.call_args[1]["body"]
        assert body["tenant_id"] == TENANT_A
        assert body["status"] == "trial"
        assert body["plan_id"] == "starter_monthly"
        assert body["trial_days_granted"] == 30
        assert body["auto_renew"] is False

    def test_emits_trial_started_event(self):
        with patch(SUBS_MOD), \
             patch(EVENTS_MOD) as mock_events, \
             patch(TENANTS_MOD) as mock_tenants:
            mock_tenants.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import create_trial_subscription
            create_trial_subscription(TENANT_A)

        mock_events.create_item.assert_called_once()
        event = mock_events.create_item.call_args[1]["body"]
        assert event["type"] == "subscription.trial_started"
        assert event["tenant_id"] == TENANT_A

    def test_syncs_tenant_plan_on_creation(self):
        tenant_doc = {"id": TENANT_A, "plan": "free"}
        with patch(SUBS_MOD), \
             patch(EVENTS_MOD), \
             patch(TENANTS_MOD) as mock_tenants:
            mock_tenants.query_items.return_value = [tenant_doc]
            from smart_invoice_pro.services.subscription_service import create_trial_subscription
            create_trial_subscription(TENANT_A)

        mock_tenants.replace_item.assert_called_once()
        updated = mock_tenants.replace_item.call_args[1]["body"]
        assert updated["plan"] == "trial"

    def test_returns_existing_sub_on_duplicate(self):
        existing_sub = _make_sub()
        with patch(SUBS_MOD) as mock_subs, \
             patch(EVENTS_MOD), \
             patch(TENANTS_MOD) as mock_tenants:
            mock_subs.create_item.side_effect = Exception("Conflict")
            mock_subs.query_items.return_value = [existing_sub]
            mock_tenants.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import create_trial_subscription
            result = create_trial_subscription(TENANT_A)

        assert result["id"] == existing_sub["id"]


# ─────────────────────────────────────────────────────────────────────────────
#  get_entitlements
# ─────────────────────────────────────────────────────────────────────────────

class TestGetEntitlements:

    def _mock_active_trial(self, mock_subs, mock_plans, mock_usage,
                           status="trial", invoices_created=0):
        mock_subs.query_items.return_value = [_make_sub(status=status)]
        mock_plans.query_items.return_value = [_make_plan()]
        mock_usage.query_items.return_value = [{
            "id": f"usage_{TENANT_A}_2026_06",
            "tenant_id": TENANT_A,
            "metrics": {"invoices_created": invoices_created, "users_active": 0},
        }]

    def test_active_trial_can_create(self):
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            self._mock_active_trial(ms, mp, mu)
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["can_create"] is True
        assert result["read_only"] is False
        assert result["can_create_invoice"] is True

    def test_expired_status_blocks_creation(self):
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            ms.query_items.return_value = [_make_sub(status="expired")]
            mp.query_items.return_value = []
            mu.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["read_only"] is True
        assert result["upgrade_required"] is True

    def test_no_subscription_returns_can_create_false(self):
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            ms.query_items.return_value = []
            mp.query_items.return_value = []
            mu.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["status"] == "no_subscription"
        assert result["can_create"] is False

    def test_invoice_limit_reached_blocks_create_invoice(self):
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            self._mock_active_trial(ms, mp, mu, invoices_created=100)
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["can_create_invoice"] is False

    def test_under_invoice_limit_allows_create(self):
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            self._mock_active_trial(ms, mp, mu, invoices_created=99)
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["can_create_invoice"] is True

    def test_trial_extension_statuses_are_active(self):
        for status in ("trial_extension_1", "trial_extension_2", "founding_customer"):
            with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
                self._mock_active_trial(ms, mp, mu, status=status)
                from smart_invoice_pro.services.subscription_service import get_entitlements
                result = get_entitlements(TENANT_A)
            assert result["can_create"] is True, f"Expected can_create for status={status}"

    def test_days_remaining_is_computed(self):
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        sub = _make_sub(status="trial", trial_ends_at=future)
        with patch(SUBS_MOD) as ms, patch(PLANS_MOD) as mp, patch(USAGE_MOD) as mu:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [_make_plan()]
            mu.query_items.return_value = [{"metrics": {}}]
            from smart_invoice_pro.services.subscription_service import get_entitlements
            result = get_entitlements(TENANT_A)
        assert result["days_remaining"] in (6, 7)  # boundary-safe


# ─────────────────────────────────────────────────────────────────────────────
#  request_extension
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestExtension:

    def test_extension_1_auto_approves(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            result = request_extension(TENANT_A, "1", reason="")
        assert result["status"] == "granted"
        assert result["extension_type"] == "1"

    def test_extension_1_extends_trial_by_30_days(self):
        original_end = datetime.now(timezone.utc) + timedelta(days=10)
        sub = _make_sub(trial_ends_at=original_end.isoformat())
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            result = request_extension(TENANT_A, "1", reason="")
        new_end = datetime.fromisoformat(result["new_trial_ends_at"])
        diff = (new_end - original_end).days
        assert diff == 30

    def test_extension_1_cannot_be_requested_twice(self):
        sub = _make_sub(
            trial_extension_1_requested_at=datetime.now(timezone.utc).isoformat()
        )
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            with pytest.raises(ValueError, match="already granted"):
                request_extension(TENANT_A, "1", reason="")

    def test_extension_2_queues_for_admin(self):
        now = datetime.now(timezone.utc).isoformat()
        sub = _make_sub(trial_extension_1_requested_at=now)
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), patch(EXT_MOD) as mext:
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            result = request_extension(TENANT_A, "2", reason="need more time")
        assert result["status"] == "pending_approval"
        mext.create_item.assert_called_once()
        ext_body = mext.create_item.call_args[1]["body"]
        assert ext_body["status"] == "pending"
        assert ext_body["reason"] == "need more time"

    def test_extension_2_requires_extension_1_first(self):
        sub = _make_sub()  # no extension 1
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            with pytest.raises(ValueError, match="Extension 1 must be used"):
                request_extension(TENANT_A, "2", reason="")

    def test_extension_on_non_trial_raises(self):
        sub = _make_sub(status="active")
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            with pytest.raises(ValueError, match="only valid for trial"):
                request_extension(TENANT_A, "1", reason="")

    def test_invalid_extension_type_raises(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import request_extension
            with pytest.raises(ValueError, match="extension_type must be"):
                request_extension(TENANT_A, "3", reason="")


# ─────────────────────────────────────────────────────────────────────────────
#  activate_subscription
# ─────────────────────────────────────────────────────────────────────────────

class TestActivateSubscription:

    def test_activates_subscription(self):
        sub = _make_sub()
        plan = _make_plan()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), \
             patch(PLANS_MOD) as mp, patch(TENANTS_MOD) as mt:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [plan]
            mt.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import activate_subscription
            result = activate_subscription(
                TENANT_A, "starter_monthly", "monthly", 599.0
            )
        assert result["status"] == "active"
        assert result["amount_inr"] == 599.0
        assert result["auto_renew"] is True

    def test_activate_sets_gateway_info(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), \
             patch(PLANS_MOD) as mp, patch(TENANTS_MOD) as mt:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [_make_plan()]
            mt.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import activate_subscription
            result = activate_subscription(
                TENANT_A, "starter_monthly", "monthly", 599.0,
                gateway="zoho_payments", gateway_sub_id="link_abc123"
            )
        assert result["payment_gateway"] == "zoho_payments"
        assert result["gateway_subscription_id"] == "link_abc123"

    def test_activate_raises_if_no_subscription(self):
        with patch(SUBS_MOD) as ms:
            ms.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import activate_subscription
            with pytest.raises(ValueError, match="No subscription found"):
                activate_subscription(TENANT_A, "starter_monthly", "monthly", 599.0)

    def test_annual_cycle_sets_365_day_period(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), \
             patch(PLANS_MOD) as mp, patch(TENANTS_MOD) as mt:
            ms.query_items.return_value = [sub]
            mp.query_items.return_value = [_make_plan()]
            mt.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import activate_subscription
            result = activate_subscription(TENANT_A, "starter_monthly", "annual", 5999.0)
        end = datetime.fromisoformat(result["current_period_end"])
        diff = (end - datetime.now(timezone.utc)).days
        assert 363 <= diff <= 366


# ─────────────────────────────────────────────────────────────────────────────
#  expire_trial
# ─────────────────────────────────────────────────────────────────────────────

class TestExpireTrial:

    def test_sets_status_to_expired(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), patch(TENANTS_MOD) as mt:
            ms.query_items.return_value = [sub]
            mt.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import expire_trial
            result = expire_trial(TENANT_A)
        assert result["status"] == "expired"

    def test_suspends_tenant(self):
        sub = _make_sub()
        tenant_doc = {"id": TENANT_A, "status": "active"}
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), patch(TENANTS_MOD) as mt:
            ms.query_items.return_value = [sub]
            mt.query_items.return_value = [tenant_doc]
            from smart_invoice_pro.services.subscription_service import expire_trial
            expire_trial(TENANT_A)
        mt.replace_item.assert_called()
        updated_tenant = mt.replace_item.call_args[1]["body"]
        assert updated_tenant["status"] == "suspended"

    def test_returns_empty_dict_when_no_sub(self):
        with patch(SUBS_MOD) as ms:
            ms.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import expire_trial
            result = expire_trial(TENANT_A)
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
#  cancel_subscription
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelSubscription:

    def test_sets_status_to_cancelled(self):
        sub = _make_sub(status="active")
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import cancel_subscription
            result = cancel_subscription(TENANT_A, reason="too expensive")
        assert result["status"] == "cancelled"
        assert result["cancel_reason"] == "too expensive"
        assert result["auto_renew"] is False
        assert result["cancelled_at"] is not None

    def test_cancel_raises_when_no_sub(self):
        with patch(SUBS_MOD) as ms:
            ms.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import cancel_subscription
            with pytest.raises(ValueError, match="No subscription found"):
                cancel_subscription(TENANT_A, reason="")


# ─────────────────────────────────────────────────────────────────────────────
#  grant_founding_customer
# ─────────────────────────────────────────────────────────────────────────────

class TestGrantFoundingCustomer:

    def test_sets_founding_customer_flag(self):
        sub = _make_sub()
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), patch(PLANS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import grant_founding_customer
            result = grant_founding_customer(TENANT_A, "admin-001", notes="VIP")
        assert result["is_founding_customer"] is True
        assert result["status"] == "founding_customer"
        assert result["founding_customer_granted_by"] == "admin-001"

    def test_sets_trial_end_to_180_days_from_signup(self):
        signup = datetime.now(timezone.utc) - timedelta(days=10)
        sub = _make_sub(created_at=signup.isoformat())
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD), patch(PLANS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import grant_founding_customer
            result = grant_founding_customer(TENANT_A, "admin-001")
        end = datetime.fromisoformat(result["trial_ends_at"])
        expected = signup + timedelta(days=180)
        diff = abs((end - expected).total_seconds())
        assert diff < 2  # within 2 seconds


# ─────────────────────────────────────────────────────────────────────────────
#  grant_admin_extension
# ─────────────────────────────────────────────────────────────────────────────

class TestGrantAdminExtension:

    def test_extends_trial_end_by_requested_days(self):
        original_end = datetime.now(timezone.utc) + timedelta(days=5)
        sub = _make_sub(trial_ends_at=original_end.isoformat())
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import grant_admin_extension
            result = grant_admin_extension(TENANT_A, 14, "admin-001", reason="support request")
        new_end = datetime.fromisoformat(result["trial_ends_at"])
        diff = (new_end - original_end).days
        assert diff == 14

    def test_accumulates_extended_days(self):
        sub = _make_sub(trial_extended_by_days=30)
        with patch(SUBS_MOD) as ms, patch(EVENTS_MOD):
            ms.query_items.return_value = [sub]
            from smart_invoice_pro.services.subscription_service import grant_admin_extension
            result = grant_admin_extension(TENANT_A, 7, "admin-001")
        assert result["trial_extended_by_days"] == 37


# ─────────────────────────────────────────────────────────────────────────────
#  get_current_usage / increment_usage
# ─────────────────────────────────────────────────────────────────────────────

class TestUsage:

    def test_get_current_usage_returns_existing_record(self):
        existing = {
            "id": f"usage_{TENANT_A}_2026_06",
            "tenant_id": TENANT_A,
            "metrics": {"invoices_created": 5},
        }
        with patch(USAGE_MOD) as mu:
            mu.query_items.return_value = [existing]
            from smart_invoice_pro.services.subscription_service import get_current_usage
            result = get_current_usage(TENANT_A)
        assert result["metrics"]["invoices_created"] == 5

    def test_get_current_usage_bootstraps_new_record(self):
        with patch(USAGE_MOD) as mu:
            mu.query_items.return_value = []
            from smart_invoice_pro.services.subscription_service import get_current_usage
            result = get_current_usage(TENANT_A)
        mu.create_item.assert_called_once()
        assert result["metrics"]["invoices_created"] == 0

    def test_increment_usage_adds_to_metric(self):
        existing = {
            "id": f"usage_{TENANT_A}_2026_06",
            "tenant_id": TENANT_A,
            "metrics": {"invoices_created": 3},
            "updated_at": "2026-06-01T00:00:00+00:00",
        }
        with patch(USAGE_MOD) as mu:
            mu.query_items.return_value = [existing]
            from smart_invoice_pro.services.subscription_service import increment_usage
            increment_usage(TENANT_A, "invoices_created")
        mu.replace_item.assert_called_once()
        saved = mu.replace_item.call_args[1]["body"]
        assert saved["metrics"]["invoices_created"] == 4
