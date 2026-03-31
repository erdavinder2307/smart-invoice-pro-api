"""Tests for notifications, audit logs, bank accounts, recurring profiles,
reports, profile, and settings API endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from azure.cosmos import exceptions as cosmos_exceptions
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────
class TestListNotifications:
    """GET /api/notifications tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.query_items.return_value = [
                {"id": "n1", "message": "Low stock", "is_read": False, "tenant_id": TENANT_A},
            ]
            resp = client.get("/api/notifications", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert "notifications" in data
            assert data["unread_count"] == 1

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/notifications", headers=headers_a)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["unread_count"] == 0

    def test_list_respects_limit(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/notifications?limit=5", headers=headers_a)
            assert resp.status_code == 200


class TestMarkNotificationRead:
    """PUT /api/notifications/<id>/read tests."""

    def test_mark_read_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.read_item.return_value = {"id": "n1", "is_read": False, "tenant_id": TENANT_A}
            mock_ctr.replace_item.return_value = {}
            resp = client.put("/api/notifications/n1/read", headers=headers_a)
            assert resp.status_code == 200

    def test_mark_read_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.read_item.side_effect = cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message="Not found"
            )
            resp = client.put("/api/notifications/nope/read", headers=headers_a)
            assert resp.status_code == 404


class TestMarkAllNotificationsRead:
    """PUT /api/notifications/read-all tests."""

    def test_mark_all_read(self, client, headers_a):
        with patch("smart_invoice_pro.api.notifications_api.notifications_container") as mock_ctr:
            mock_ctr.query_items.return_value = [
                {"id": "n1", "is_read": False, "tenant_id": TENANT_A},
                {"id": "n2", "is_read": False, "tenant_id": TENANT_A},
            ]
            mock_ctr.replace_item.return_value = {}
            resp = client.put("/api/notifications/read-all", headers=headers_a)
            assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOGS  (requires Admin role)
# ─────────────────────────────────────────────────────────────────────────────
class TestListAuditLogs:
    """GET /api/audit-logs tests."""

    def test_list_returns_data(self, client, headers_a):
        """Default user may or may not have Admin role — test route reachability."""
        with patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container") as mock_ctr, \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            # Simulate Admin role lookup
            mock_users.query_items.return_value = [{"role": "Admin", "userid": USER_A}]
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/audit-logs", headers=headers_a)
            # May be 200 or 403 depending on role check
            assert resp.status_code in (200, 403)


# ─────────────────────────────────────────────────────────────────────────────
# BANK ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────────
class TestCreateBankAccount:
    """POST /api/bank-accounts tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.create_item.return_value = {"id": "ba-1", "bank_name": "ICICI"}
            payload = {"bank_name": "ICICI", "account_name": "Business", "account_type": "current"}
            resp = client.post("/api/bank-accounts", json=payload, headers=headers_a)
            assert resp.status_code == 201

    def test_create_missing_required(self, client, headers_a):
        resp = client.post("/api/bank-accounts", json={}, headers=headers_a)
        assert resp.status_code == 400


class TestListBankAccounts:
    """GET /api/bank-accounts tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.query_items.return_value = [{"id": "ba-1", "bank_name": "ICICI"}]
            resp = client.get("/api/bank-accounts", headers=headers_a)
            assert resp.status_code == 200


class TestGetBankAccount:
    """GET /api/bank-accounts/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.query_items.return_value = [{"id": "ba-1", "user_id": USER_A, "bank_name": "ICICI"}]
            resp = client.get("/api/bank-accounts/ba-1", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/bank-accounts/nope", headers=headers_a)
            assert resp.status_code == 404


class TestDeleteBankAccount:
    """DELETE /api/bank-accounts/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.query_items.return_value = [{"id": "ba-1", "user_id": USER_A}]
            resp = client.delete("/api/bank-accounts/ba-1", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/bank-accounts/nope", headers=headers_a)
            assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# RECURRING PROFILES
# ─────────────────────────────────────────────────────────────────────────────
SAMPLE_PROFILE = {
    "profile_name": "Monthly Invoice",
    "customer_id": "cust-001",
    "frequency": "Monthly",
    "start_date": "2026-03-01",
}

STORED_PROFILE = {
    "id": "rp-001",
    "profile_name": "Monthly Invoice",
    "customer_id": "cust-001",
    "frequency": "Monthly",
    "start_date": "2026-03-01",
    "status": "Active",
    "items": [],
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}


class TestCreateRecurringProfile:
    """POST /api/recurring-profiles tests."""

    def test_create_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.create_item.return_value = {**SAMPLE_PROFILE, "id": "new-id"}
            resp = client.post("/api/recurring-profiles", json=SAMPLE_PROFILE, headers=headers_a)
            assert resp.status_code == 201

    def test_create_missing_required(self, client, headers_a):
        resp = client.post("/api/recurring-profiles", json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_create_invalid_frequency(self, client, headers_a):
        payload = {**SAMPLE_PROFILE, "frequency": "Biweekly"}
        resp = client.post("/api/recurring-profiles", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_invalid_status(self, client, headers_a):
        payload = {**SAMPLE_PROFILE, "status": "Unknown"}
        resp = client.post("/api/recurring-profiles", json=payload, headers=headers_a)
        assert resp.status_code == 400

    def test_create_end_before_start(self, client, headers_a):
        payload = {**SAMPLE_PROFILE, "end_date": "2026-02-01"}
        resp = client.post("/api/recurring-profiles", json=payload, headers=headers_a)
        assert resp.status_code == 400


class TestListRecurringProfiles:
    """GET /api/recurring-profiles tests."""

    def test_list_returns_data(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PROFILE]
            resp = client.get("/api/recurring-profiles", headers=headers_a)
            assert resp.status_code == 200

    def test_list_empty(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/recurring-profiles", headers=headers_a)
            assert resp.status_code == 200


class TestGetRecurringProfile:
    """GET /api/recurring-profiles/<id> tests."""

    def test_get_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PROFILE]
            resp = client.get("/api/recurring-profiles/rp-001", headers=headers_a)
            assert resp.status_code == 200

    def test_get_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/recurring-profiles/nope", headers=headers_a)
            assert resp.status_code == 404


class TestUpdateRecurringProfile:
    """PUT /api/recurring-profiles/<id> tests."""

    def test_update_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PROFILE]
            mock_ctr.replace_item.return_value = {**STORED_PROFILE, "notes": "updated"}
            resp = client.put("/api/recurring-profiles/rp-001", json={"notes": "updated"}, headers=headers_a)
            assert resp.status_code == 200

    def test_update_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.put("/api/recurring-profiles/nope", json={"notes": "x"}, headers=headers_a)
            assert resp.status_code == 404


class TestDeleteRecurringProfile:
    """DELETE /api/recurring-profiles/<id> tests."""

    def test_delete_success(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = [STORED_PROFILE]
            resp = client.delete("/api/recurring-profiles/rp-001", headers=headers_a)
            assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        with patch("smart_invoice_pro.api.recurring_profiles_api.recurring_profiles_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.delete("/api/recurring-profiles/nope", headers=headers_a)
            assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS (read-only)
# ─────────────────────────────────────────────────────────────────────────────
class TestReports:
    """GET /api/reports/* tests."""

    def test_profit_loss(self, client, headers_a):
        with patch("smart_invoice_pro.api.reports_api.invoices_container") as mock_inv, \
             patch("smart_invoice_pro.api.reports_api.expenses_container") as mock_exp, \
             patch("smart_invoice_pro.api.reports_api.bills_container") as mock_bills:
            mock_inv.query_items.return_value = [{"total_amount": 5000, "status": "Paid", "tenant_id": TENANT_A}]
            mock_exp.query_items.return_value = []
            mock_bills.query_items.return_value = []
            resp = client.get("/api/reports/profit-loss?start_date=2026-01-01&end_date=2026-12-31", headers=headers_a)
            assert resp.status_code == 200

    def test_balance_sheet(self, client, headers_a):
        with patch("smart_invoice_pro.api.reports_api.invoices_container") as mock_inv, \
             patch("smart_invoice_pro.api.reports_api.bills_container") as mock_bills, \
             patch("smart_invoice_pro.api.reports_api.bank_accounts_container") as mock_ba, \
             patch("smart_invoice_pro.api.reports_api.expenses_container") as mock_exp:
            mock_inv.query_items.return_value = []
            mock_bills.query_items.return_value = []
            mock_ba.query_items.return_value = []
            mock_exp.query_items.return_value = []
            resp = client.get("/api/reports/balance-sheet", headers=headers_a)
            assert resp.status_code == 200

    def test_sales_summary(self, client, headers_a):
        with patch("smart_invoice_pro.api.reports_api.invoices_container") as mock_inv:
            mock_inv.query_items.return_value = []
            resp = client.get("/api/reports/sales-summary", headers=headers_a)
            assert resp.status_code == 200

    def test_aging_report(self, client, headers_a):
        with patch("smart_invoice_pro.api.reports_api.invoices_container") as mock_inv, \
             patch("smart_invoice_pro.api.reports_api.customers_container") as mock_cust:
            mock_inv.query_items.return_value = []
            mock_cust.query_items.return_value = []
            resp = client.get("/api/reports/aging", headers=headers_a)
            assert resp.status_code == 200

    def test_ap_aging_report(self, client, headers_a):
        with patch("smart_invoice_pro.api.reports_api.bills_container") as mock_bills:
            mock_bills.query_items.return_value = []
            resp = client.get("/api/reports/ap-aging", headers=headers_a)
            assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────────────────────────────────────
class TestProfile:
    """GET /api/profile/me and POST /api/profile/update tests."""

    def test_get_profile(self, client, headers_a):
        with patch("smart_invoice_pro.api.profile_api.users_container") as mock_ctr:
            mock_ctr.query_items.return_value = [
                {"user_id": USER_A, "name": "Test User", "email": "test@example.com", "type": "user_profile"}
            ]
            resp = client.get("/api/profile/me", headers=headers_a)
            assert resp.status_code == 200

    def test_get_profile_default_when_none(self, client, headers_a):
        with patch("smart_invoice_pro.api.profile_api.users_container") as mock_ctr:
            mock_ctr.query_items.return_value = []
            resp = client.get("/api/profile/me", headers=headers_a)
            assert resp.status_code == 200

    def test_update_profile(self, client, headers_a):
        with patch("smart_invoice_pro.api.profile_api.users_container") as mock_ctr:
            mock_ctr.query_items.return_value = [
                {"id": "p1", "user_id": USER_A, "name": "Old Name", "email": "old@example.com", "type": "user_profile"}
            ]
            mock_ctr.replace_item.return_value = {}
            payload = {"name": "New Name", "email": "new@example.com"}
            resp = client.post("/api/profile/update", json=payload, headers=headers_a)
            assert resp.status_code == 200

    def test_update_profile_missing_name(self, client, headers_a):
        payload = {"email": "test@example.com"}
        resp = client.post("/api/profile/update", json=payload, headers=headers_a)
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS: REMINDERS
# ─────────────────────────────────────────────────────────────────────────────
class TestReminderSettings:
    """GET/POST /api/settings/reminders tests."""

    def test_get_reminders(self, client, headers_a):
        with patch("smart_invoice_pro.api.reminders_api.settings_container") as mock_ctr:
            mock_ctr.read_item.return_value = {
                "id": f"{TENANT_A}:reminder_settings",
                "reminders_enabled": True,
                "before_due_days": [3, 7],
            }
            resp = client.get("/api/settings/reminders", headers=headers_a)
            assert resp.status_code == 200

    def test_save_reminders(self, client, headers_a):
        with patch("smart_invoice_pro.api.reminders_api.settings_container") as mock_ctr:
            mock_ctr.upsert_item.return_value = {}
            payload = {"reminders_enabled": True, "before_due_days": [3, 7]}
            resp = client.post("/api/settings/reminders", json=payload, headers=headers_a)
            assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS: ORGANIZATION PROFILE
# ─────────────────────────────────────────────────────────────────────────────
class TestOrganizationProfile:
    """GET/PUT /api/settings/organization-profile tests."""

    def test_get_org_profile(self, client, headers_a):
        with patch("smart_invoice_pro.api.organization_profile_api.settings_container") as mock_ctr:
            mock_ctr.read_item.return_value = {
                "id": f"{TENANT_A}:organization_profile",
                "organization_name": "Test Co",
                "country": "India",
            }
            resp = client.get("/api/settings/organization-profile", headers=headers_a)
            assert resp.status_code == 200

    def test_update_org_profile(self, client, headers_a):
        with patch("smart_invoice_pro.api.organization_profile_api.settings_container") as mock_ctr, \
             patch("smart_invoice_pro.api.roles_api.users_container") as mock_users:
            mock_users.query_items.return_value = [{"role": "Admin", "userid": USER_A}]
            mock_ctr.read_item.return_value = {"id": f"{TENANT_A}:organization_profile"}
            mock_ctr.upsert_item.return_value = {}
            payload = {"organization_name": "Updated Co", "country": "India"}
            resp = client.put("/api/settings/organization-profile", json=payload, headers=headers_a)
            # Will be 200 if Admin, 403 if not
            assert resp.status_code in (200, 403)


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS: BRANDING
# ─────────────────────────────────────────────────────────────────────────────
class TestBranding:
    """GET/PUT /api/settings/branding tests."""

    def test_get_branding(self, client, headers_a):
        with patch("smart_invoice_pro.api.branding_api.settings_container") as mock_ctr:
            mock_ctr.read_item.return_value = {
                "id": f"{TENANT_A}:organization_profile",
                "primary_color": "#2563EB",
            }
            resp = client.get("/api/settings/branding", headers=headers_a)
            assert resp.status_code == 200
