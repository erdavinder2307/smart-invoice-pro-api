"""
Tests for the end of the free trial and of a lapsed payment (utils/entitlements.py):
account_lock_code on its own, the write lock in enforce_api_auth, new trial end dates
(utils/tenant_service.py) and scripts/set_trial_end_dates.py.
"""
import importlib.util
import pathlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from smart_invoice_pro.utils import entitlements
from smart_invoice_pro.utils.tenant_service import TRIAL_DAYS, create_tenant_doc
from tests.conftest import TENANT_A, auth_headers

NOW = datetime(2026, 11, 10, 12, 0, 0)

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "set_trial_end_dates.py"
_spec = importlib.util.spec_from_file_location("set_trial_end_dates", SCRIPT)
trial_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trial_script)


def _tenant(**fields):
    base = {"id": TENANT_A, "name": "Acme", "plan": "trial", "status": "active", "tenant_type": "PRODUCTION"}
    base.update(fields)
    return base


class TestAccountLockCode:
    def test_trial_before_end_is_open(self):
        t = _tenant(trial_ends_at=(NOW + timedelta(days=1)).isoformat())
        assert entitlements.account_lock_code(t, NOW) is None

    def test_trial_at_end_is_open(self):
        t = _tenant(trial_ends_at=NOW.isoformat())
        assert entitlements.account_lock_code(t, NOW) is None

    def test_trial_after_end_is_locked(self):
        t = _tenant(trial_ends_at=(NOW - timedelta(seconds=1)).isoformat())
        assert entitlements.account_lock_code(t, NOW) == "trial_ended"

    def test_trial_end_with_offset_is_read_as_utc(self):
        # 17:00 at +05:30 is 11:30 UTC, before NOW (12:00 UTC).
        t = _tenant(trial_ends_at="2026-11-10T17:00:00+05:30")
        assert entitlements.account_lock_code(t, NOW) == "trial_ended"
        t = _tenant(trial_ends_at="2026-11-10T12:30:00Z")
        assert entitlements.account_lock_code(t, NOW) is None

    def test_trial_without_end_date_is_open(self):
        # Older organisations until scripts/set_trial_end_dates.py runs.
        assert entitlements.account_lock_code(_tenant(), NOW) is None

    def test_unreadable_end_date_is_open(self):
        assert entitlements.account_lock_code(_tenant(trial_ends_at="soon"), NOW) is None

    def test_trial_with_active_payment_is_open(self):
        t = _tenant(trial_ends_at=(NOW - timedelta(days=5)).isoformat(), billing={"status": "active"})
        assert entitlements.account_lock_code(t, NOW) is None

    def test_paid_plan_is_open(self):
        t = _tenant(plan="starter", trial_ends_at=(NOW - timedelta(days=60)).isoformat())
        assert entitlements.account_lock_code(t, NOW) is None

    @pytest.mark.parametrize("status", ["halted", "cancelled"])
    def test_failed_payment_inside_grace_is_open(self, status):
        t = _tenant(plan="growth", billing={"status": status},
                    plan_period_end=(NOW - timedelta(days=7)).isoformat())
        assert entitlements.account_lock_code(t, NOW) is None

    @pytest.mark.parametrize("status", ["halted", "cancelled"])
    def test_failed_payment_after_grace_is_locked(self, status):
        t = _tenant(plan="growth", billing={"status": status},
                    plan_period_end=(NOW - timedelta(days=7, seconds=1)).isoformat())
        assert entitlements.account_lock_code(t, NOW) == "payment_failed"

    def test_failed_payment_without_period_end_is_open(self):
        t = _tenant(plan="growth", billing={"status": "halted"})
        assert entitlements.account_lock_code(t, NOW) is None


def _post_customer(client, tenant, headers=None, path="/api/customers", method="post"):
    with patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=tenant):
        return getattr(client, method)(path, json={}, headers=headers or auth_headers())


EXPIRED = _tenant(trial_ends_at="2020-01-01T00:00:00")
OPEN = _tenant(trial_ends_at="2999-01-01T00:00:00")


class TestWriteLock:
    def test_write_after_trial_end_returns_402(self, client):
        resp = _post_customer(client, EXPIRED)
        assert resp.status_code == 402
        body = resp.get_json()
        assert body["error"] == "plan_limit"
        assert body["code"] == "trial_ended"
        assert body["upgrade_url"] == "/settings/billing"
        assert "still here" in body["message"]

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_other_writes_are_locked_too(self, client, method):
        resp = _post_customer(client, EXPIRED, path="/api/customers/c1", method=method)
        assert resp.status_code == 402

    def test_write_during_trial_is_not_locked(self, client):
        assert _post_customer(client, OPEN).status_code != 402

    def test_reads_stay_open_after_trial_end(self, client):
        assert _post_customer(client, EXPIRED, method="get").status_code != 402

    def test_logout_stays_open_after_trial_end(self, client):
        assert _post_customer(client, EXPIRED, path="/api/auth/logout").status_code != 402

    def test_billing_paths_stay_open_after_trial_end(self, client):
        # No billing routes yet: the lock lets the request through to the router (404).
        assert _post_customer(client, EXPIRED, path="/api/billing/checkout").status_code == 404

    def test_payment_failed_after_grace_returns_402(self, client):
        tenant = _tenant(plan="growth", billing={"status": "halted"}, plan_period_end="2020-01-01T00:00:00")
        resp = _post_customer(client, tenant)
        assert resp.status_code == 402
        assert resp.get_json()["code"] == "payment_failed"

    @pytest.mark.parametrize("tenant_type", ["DEMO", "INTERNAL"])
    def test_demo_and_internal_tenants_are_exempt(self, client, tenant_type):
        assert _post_customer(client, dict(EXPIRED, tenant_type=tenant_type)).status_code != 402

    def test_demo_session_is_exempt(self, client):
        assert _post_customer(client, EXPIRED, headers=auth_headers(is_demo=True)).status_code != 402

    def test_super_admin_is_exempt(self, client):
        assert _post_customer(client, EXPIRED, headers=auth_headers(is_super_admin=True)).status_code != 402

    def test_missing_tenant_document_is_not_locked(self, client):
        assert _post_customer(client, None).status_code != 402

    def test_unauthenticated_write_is_still_401(self, client):
        with patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=EXPIRED):
            assert client.post("/api/customers", json={}).status_code == 401


class TestNewTenantTrialEnd:
    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_trial_tenant_gets_end_date(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        doc = create_tenant_doc(name="Acme")
        created = datetime.fromisoformat(doc["created_at"])
        assert datetime.fromisoformat(doc["trial_ends_at"]) - created == timedelta(days=TRIAL_DAYS)
        assert TRIAL_DAYS == 30
        assert mock_ctr.create_item.call_args.kwargs["body"]["trial_ends_at"] == doc["trial_ends_at"]

    @patch("smart_invoice_pro.utils.tenant_service.tenants_container")
    def test_paid_tenant_gets_no_trial_end(self, mock_ctr):
        mock_ctr.query_items.return_value = []
        assert "trial_ends_at" not in create_tenant_doc(name="Acme", plan="starter")


class TestSetTrialEndDatesScript:
    TENANTS = [
        {"id": "t1", "name": "Open trial", "plan": "trial", "created_at": "2026-05-01T00:00:00"},
        {"id": "t2", "name": "No plan field", "created_at": "2026-06-01T00:00:00"},
        {"id": "t3", "name": "Has end", "plan": "trial", "trial_ends_at": "2026-12-01T00:00:00"},
        {"id": "t4", "name": "Paid", "plan": "starter"},
        {"id": "t5", "name": "Demo", "plan": "trial", "tenant_type": "DEMO"},
        {"id": "t6", "name": "Internal", "plan": "trial", "tenant_type": "internal"},
    ]

    def _container(self):
        ctr = MagicMock()
        ctr.query_items.return_value = [dict(t) for t in self.TENANTS]
        return ctr

    def test_dry_run_lists_open_trials_and_writes_nothing(self):
        ctr, lines = self._container(), []
        targets = trial_script.run(ctr, "2026-11-15T00:00:00", apply=False, out=lines.append)
        assert [t["id"] for t in targets] == ["t1", "t2"]
        assert "DRY RUN: 2 of 6" in lines[0]
        assert "2026-11-15T00:00:00" in lines[1]
        ctr.replace_item.assert_not_called()

    def test_apply_writes_only_the_end_date(self):
        ctr = self._container()
        trial_script.run(ctr, "2026-11-15T00:00:00", apply=True, now=NOW, out=lambda _: None)
        assert ctr.replace_item.call_count == 2
        body = ctr.replace_item.call_args_list[0].kwargs["body"]
        assert body == {"id": "t1", "name": "Open trial", "plan": "trial", "created_at": "2026-05-01T00:00:00",
                        "trial_ends_at": "2026-11-15T00:00:00", "updated_at": NOW.isoformat()}

    def test_end_date_must_be_a_future_day(self):
        with pytest.raises(SystemExit):
            trial_script.main(["--end-date", "2020-01-01"])
        with pytest.raises(SystemExit):
            trial_script.main(["--end-date", "15/11/2026"])
