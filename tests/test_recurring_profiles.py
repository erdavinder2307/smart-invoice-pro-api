"""Tests for recurring_profiles_api.py — CRUD, status transitions, bulk, and date logic."""
import pytest
from unittest.mock import patch, MagicMock, call

from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B

MODULE = "smart_invoice_pro.api.recurring_profiles_api"

# ── Shared fixtures ──────────────────────────────────────────────────────────

VALID_PAYLOAD = {
    "profile_name": "Monthly Maintenance",
    "customer_id": "cust-001",
    "customer_name": "ACME Corp",
    "frequency": "Monthly",
    "start_date": "2024-01-01",
    "items": [{"name": "Maintenance", "quantity": 1, "rate": 500.0}],
    "ends_type": "never",
}

STORED_PROFILE = {
    "id": "prof-001",
    "tenant_id": TENANT_A,
    "profile_name": "Monthly Maintenance",
    "customer_id": "cust-001",
    "customer_name": "ACME Corp",
    "frequency": "Monthly",
    "start_date": "2024-01-01",
    "next_run_date": "2024-02-01",
    "status": "Active",
    "ends_type": "never",
    "occurrence_limit": None,
    "occurrences_created": 0,
    "auto_send": False,
    "items": [{"name": "Maintenance", "quantity": 1, "rate": 500.0}],
}


# ── calculate_next_run_date unit tests (pure function, no HTTP) ───────────────

class TestCalculateNextRunDate:
    """Unit tests for the calculate_next_run_date helper — no Flask app needed."""

    def _calc(self, current, frequency, rule=None):
        from smart_invoice_pro.api.recurring_profiles_api import calculate_next_run_date
        return calculate_next_run_date(current, frequency, rule)

    def test_daily_advances_one_day(self):
        assert self._calc("2024-01-15", "Daily") == "2024-01-16"

    def test_daily_with_interval_3(self):
        assert self._calc("2024-01-15", "Daily", {"interval": 3}) == "2024-01-18"

    def test_weekly_advances_one_week(self):
        assert self._calc("2024-01-15", "Weekly") == "2024-01-22"

    def test_weekly_with_interval_2(self):
        assert self._calc("2024-01-15", "Weekly", {"interval": 2}) == "2024-01-29"

    def test_weekly_with_specific_days(self):
        # 2024-01-15 is Monday (weekday=0 → Sunday=0 in our dow scheme, Monday=1)
        # weekly_days=[3] means Wednesday
        result = self._calc("2024-01-15", "Weekly", {"weekly_days": [3]})
        # Wednesday 2024-01-17
        assert result == "2024-01-17"

    def test_monthly_advances_one_month(self):
        assert self._calc("2024-01-15", "Monthly") == "2024-02-15"

    def test_monthly_with_interval_3(self):
        assert self._calc("2024-01-15", "Monthly", {"interval": 3}) == "2024-04-15"

    def test_monthly_clamps_day_at_month_end(self):
        # Jan 31 + 1 month → Feb (28 days in 2024 is leap, so 29)
        result = self._calc("2024-01-31", "Monthly")
        assert result == "2024-02-29"

    def test_monthly_non_leap_year_clamps(self):
        result = self._calc("2023-01-31", "Monthly")
        assert result == "2023-02-28"

    def test_quarterly_adds_90_days(self):
        result = self._calc("2024-01-01", "Quarterly")
        from datetime import date, timedelta
        expected = (date(2024, 1, 1) + timedelta(days=90)).isoformat()
        assert result == expected

    def test_yearly_advances_one_year(self):
        assert self._calc("2024-03-15", "Yearly") == "2025-03-15"

    def test_yearly_with_interval_2(self):
        assert self._calc("2024-03-15", "Yearly", {"interval": 2}) == "2026-03-15"

    def test_invalid_date_returns_unchanged(self):
        result = self._calc("not-a-date", "Monthly")
        assert result == "not-a-date"

    def test_unknown_frequency_returns_unchanged(self):
        result = self._calc("2024-01-15", "Biweekly")
        assert result == "2024-01-15"

    def test_case_insensitive_frequency(self):
        # lowercase 'monthly' should normalise
        result = self._calc("2024-01-15", "monthly")
        assert result == "2024-02-15"


# ── validate_recurring_profile_data unit tests ────────────────────────────────

class TestValidateRecurringProfileData:
    def _validate(self, data, is_update=False):
        from smart_invoice_pro.api.recurring_profiles_api import validate_recurring_profile_data
        return validate_recurring_profile_data(data, is_update=is_update)

    def test_valid_payload_returns_no_errors(self):
        errors = self._validate(VALID_PAYLOAD)
        assert errors == {}

    def test_missing_required_fields_on_create(self):
        errors = self._validate({})
        assert "profile_name" in errors
        assert "customer_id" in errors
        assert "frequency" in errors
        assert "start_date" in errors

    def test_invalid_frequency(self):
        data = {**VALID_PAYLOAD, "frequency": "Hourly"}
        errors = self._validate(data)
        assert "frequency" in errors

    def test_invalid_status(self):
        data = {**VALID_PAYLOAD, "status": "Pending"}
        errors = self._validate(data)
        assert "status" in errors

    def test_end_date_before_start_date(self):
        data = {
            **VALID_PAYLOAD,
            "ends_type": "on_date",
            "end_date": "2023-12-31",
            "start_date": "2024-01-01",
        }
        errors = self._validate(data)
        assert "end_date" in errors

    def test_on_date_without_end_date(self):
        data = {**VALID_PAYLOAD, "ends_type": "on_date"}
        errors = self._validate(data)
        assert "end_date" in errors

    def test_after_occurrences_without_limit(self):
        data = {**VALID_PAYLOAD, "ends_type": "after_occurrences"}
        errors = self._validate(data)
        assert "occurrence_limit" in errors

    def test_occurrence_limit_zero_invalid(self):
        data = {**VALID_PAYLOAD, "ends_type": "after_occurrences", "occurrence_limit": 0}
        errors = self._validate(data)
        assert "occurrence_limit" in errors

    def test_occurrence_limit_positive_valid(self):
        data = {**VALID_PAYLOAD, "ends_type": "after_occurrences", "occurrence_limit": 12}
        errors = self._validate(data)
        assert errors == {}

    def test_update_skips_required_field_check(self):
        # On update, missing required fields are allowed
        errors = self._validate({"profile_name": "New Name"}, is_update=True)
        assert errors == {}

    def test_non_dict_payload(self):
        errors = self._validate("not a dict")
        assert "payload" in errors


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

class TestCreateRecurringProfile:
    """POST /recurring-profiles"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_success(self, mock_ctr, client, headers_a):
        mock_ctr.create_item.return_value = {**STORED_PROFILE}
        resp = client.post("/api/recurring-profiles", json=VALID_PAYLOAD, headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["profile_name"] == "Monthly Maintenance"
        assert data["tenant_id"] == TENANT_A
        mock_ctr.create_item.assert_called_once()

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_stores_tenant_id(self, mock_ctr, client, headers_a):
        mock_ctr.create_item.return_value = {**STORED_PROFILE}
        client.post("/api/recurring-profiles", json=VALID_PAYLOAD, headers=headers_a)
        body = mock_ctr.create_item.call_args[1]["body"]
        assert body["tenant_id"] == TENANT_A

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_missing_required_fields(self, mock_ctr, client, headers_a):
        resp = client.post("/api/recurring-profiles", json={}, headers=headers_a)
        assert resp.status_code == 400
        assert "details" in resp.get_json()

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_invalid_frequency(self, mock_ctr, client, headers_a):
        payload = {**VALID_PAYLOAD, "frequency": "Hourly"}
        resp = client.post("/api/recurring-profiles", json=payload, headers=headers_a)
        assert resp.status_code == 400

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_defaults_status_to_active(self, mock_ctr, client, headers_a):
        mock_ctr.create_item.return_value = {**STORED_PROFILE}
        client.post("/api/recurring-profiles", json=VALID_PAYLOAD, headers=headers_a)
        body = mock_ctr.create_item.call_args[1]["body"]
        assert body["status"] == "Active"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_via_legacy_route(self, mock_ctr, client, headers_a):
        """POST /recurring-invoices also works."""
        mock_ctr.create_item.return_value = {**STORED_PROFILE}
        resp = client.post("/api/recurring-invoices", json=VALID_PAYLOAD, headers=headers_a)
        assert resp.status_code == 201

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_db_error_returns_500(self, mock_ctr, client, headers_a):
        mock_ctr.create_item.side_effect = Exception("DB failure")
        resp = client.post("/api/recurring-profiles", json=VALID_PAYLOAD, headers=headers_a)
        assert resp.status_code == 500

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_create_sanitizes_cosmos_fields(self, mock_ctr, client, headers_a):
        """Internal Cosmos fields (_rid, _etag) should not appear in response."""
        stored = {**STORED_PROFILE, "_rid": "abc", "_etag": "xyz", "_ts": 12345}
        mock_ctr.create_item.return_value = stored
        resp = client.post("/api/recurring-profiles", json=VALID_PAYLOAD, headers=headers_a)
        data = resp.get_json()
        assert "_rid" not in data
        assert "_etag" not in data
        assert "_ts" not in data


class TestListRecurringProfiles:
    """GET /recurring-profiles"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_list_returns_data(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [[1], [STORED_PROFILE.copy()]]
        resp = client.get("/api/recurring-profiles", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data" in data
        assert data["total"] == 1

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_list_pagination_fields(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [[5], [STORED_PROFILE.copy()]]
        resp = client.get("/api/recurring-profiles?page=2&limit=1", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["page"] == 2
        assert data["limit"] == 1

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_list_status_filter_passed_to_query(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [[0], []]
        client.get("/api/recurring-profiles?status=Active", headers=headers_a)
        # First call is count query — its parameters must include status=Active
        params = mock_ctr.query_items.call_args_list[0][1]["parameters"]
        values = [p["value"] for p in params]
        assert "Active" in values

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_list_db_error_returns_500(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = Exception("DB down")
        resp = client.get("/api/recurring-profiles", headers=headers_a)
        assert resp.status_code == 500

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_list_invalid_sort_falls_back_to_created_at(self, mock_ctr, client, headers_a):
        """An unknown sort_by value should not raise — defaults to created_at."""
        mock_ctr.query_items.side_effect = [[0], []]
        resp = client.get("/api/recurring-profiles?sort_by=hacked_field", headers=headers_a)
        assert resp.status_code == 200
        # Confirm 'hacked_field' does NOT appear in any query
        for c in mock_ctr.query_items.call_args_list:
            assert "hacked_field" not in str(c)


class TestGetRecurringProfile:
    """GET /recurring-profiles/<id>"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_get_success(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [STORED_PROFILE.copy()]
        resp = client.get("/api/recurring-profiles/prof-001", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["id"] == "prof-001"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_get_not_found(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.get("/api/recurring-profiles/nope", headers=headers_a)
        assert resp.status_code == 404

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_get_cross_tenant_not_found(self, mock_ctr, client, headers_b):
        """Tenant B cannot see Tenant A's profile — query returns empty."""
        mock_ctr.query_items.return_value = []
        resp = client.get("/api/recurring-profiles/prof-001", headers=headers_b)
        assert resp.status_code == 404


class TestUpdateRecurringProfile:
    """PUT /recurring-profiles/<id>"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_update_success(self, mock_ctr, client, headers_a):
        stored = {**STORED_PROFILE}
        updated = {**STORED_PROFILE, "profile_name": "Renamed"}
        mock_ctr.query_items.return_value = [stored]
        mock_ctr.replace_item.return_value = updated
        resp = client.put(
            "/api/recurring-profiles/prof-001",
            json={"profile_name": "Renamed"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert resp.get_json()["profile_name"] == "Renamed"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_update_not_found(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.put(
            "/api/recurring-profiles/nope",
            json={"profile_name": "X"},
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_update_archived_returns_404(self, mock_ctr, client, headers_a):
        archived = {**STORED_PROFILE, "status": "ARCHIVED"}
        mock_ctr.query_items.return_value = [archived]
        resp = client.put(
            "/api/recurring-profiles/prof-001",
            json={"profile_name": "X"},
            headers=headers_a,
        )
        assert resp.status_code == 404

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_update_ignores_id_and_tenant_id(self, mock_ctr, client, headers_a):
        stored = {**STORED_PROFILE}
        mock_ctr.query_items.return_value = [stored]
        mock_ctr.replace_item.return_value = stored
        client.put(
            "/api/recurring-profiles/prof-001",
            json={"id": "injected-id", "tenant_id": "other-tenant", "profile_name": "OK"},
            headers=headers_a,
        )
        body = mock_ctr.replace_item.call_args[1]["body"]
        assert body["id"] == "prof-001"
        assert body["tenant_id"] == TENANT_A

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_update_validation_error(self, mock_ctr, client, headers_a):
        stored = {**STORED_PROFILE}
        mock_ctr.query_items.return_value = [stored]
        resp = client.put(
            "/api/recurring-profiles/prof-001",
            json={"frequency": "InvalidFreq"},
            headers=headers_a,
        )
        assert resp.status_code == 400


class TestPatchRecurringProfile:
    """PATCH /recurring-profiles/<id>"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_patch_pause_action(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Paused"}
        resp = client.patch(
            "/api/recurring-profiles/prof-001",
            json={"action": "pause"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        body = mock_ctr.replace_item.call_args[1]["body"]
        assert body["status"] == "Paused"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_patch_resume_action(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE, "status": "Paused"}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Active"}
        resp = client.patch(
            "/api/recurring-profiles/prof-001",
            json={"action": "resume"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        body = mock_ctr.replace_item.call_args[1]["body"]
        assert body["status"] == "Active"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_patch_cancel_action(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Cancelled"}
        resp = client.patch(
            "/api/recurring-profiles/prof-001",
            json={"action": "cancel"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        body = mock_ctr.replace_item.call_args[1]["body"]
        assert body["status"] == "Cancelled"

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_patch_archived_cannot_change_status(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE, "status": "ARCHIVED"}]
        resp = client.patch(
            "/api/recurring-profiles/prof-001",
            json={"action": "resume"},
            headers=headers_a,
        )
        assert resp.status_code == 409


class TestStatusEndpoints:
    """POST /pause, /resume, PATCH /cancel"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_pause_endpoint(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Paused"}
        resp = client.post("/api/recurring-profiles/prof-001/pause", headers=headers_a)
        assert resp.status_code == 200

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_resume_endpoint(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE, "status": "Paused"}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Active"}
        resp = client.post("/api/recurring-profiles/prof-001/resume", headers=headers_a)
        assert resp.status_code == 200

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_cancel_endpoint(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_ctr.replace_item.return_value = {**STORED_PROFILE, "status": "Cancelled"}
        resp = client.patch("/api/recurring-profiles/prof-001/cancel", headers=headers_a)
        assert resp.status_code == 200

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_pause_not_found(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.post("/api/recurring-profiles/nope/pause", headers=headers_a)
        assert resp.status_code == 404


class TestDeleteRecurringProfile:
    """DELETE /recurring-profiles/<id>"""

    @patch(f"{MODULE}.apply_lifecycle_action")
    @patch(f"{MODULE}.recurring_profiles_container")
    def test_delete_archives_profile(self, mock_ctr, mock_lifecycle, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_lifecycle.return_value = {
            "performedAction": "archive",
            "status": "ARCHIVED",
            "hardDeleteAllowed": False,
            "dependencySummary": {},
        }
        resp = client.delete("/api/recurring-profiles/prof-001", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "archived" in data["message"].lower() or "deleted" in data["message"].lower()
        mock_lifecycle.assert_called_once()

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_delete_not_found(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.delete("/api/recurring-profiles/nope", headers=headers_a)
        assert resp.status_code == 404

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_delete_already_archived_returns_200(self, mock_ctr, client, headers_a):
        archived = {**STORED_PROFILE, "status": "ARCHIVED"}
        mock_ctr.query_items.return_value = [archived]
        resp = client.delete("/api/recurring-profiles/prof-001", headers=headers_a)
        assert resp.status_code == 200
        assert "already archived" in resp.get_json()["message"].lower()


class TestRestoreRecurringProfile:
    """POST /recurring-profiles/<id>/restore"""

    @patch(f"{MODULE}.restore_entity")
    @patch(f"{MODULE}.recurring_profiles_container")
    def test_restore_success(self, mock_ctr, mock_restore, client, headers_a):
        archived = {**STORED_PROFILE, "status": "ARCHIVED"}
        mock_ctr.query_items.return_value = [archived]
        mock_restore.return_value = {**STORED_PROFILE, "status": "Active"}
        resp = client.post("/api/recurring-profiles/prof-001/restore", headers=headers_a)
        assert resp.status_code == 200
        assert "restored" in resp.get_json()["message"].lower()

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_restore_not_archived_returns_422(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]  # not archived
        resp = client.post("/api/recurring-profiles/prof-001/restore", headers=headers_a)
        assert resp.status_code == 422

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_restore_not_found(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.return_value = []
        resp = client.post("/api/recurring-profiles/nope/restore", headers=headers_a)
        assert resp.status_code == 404


class TestBulkRecurringProfiles:
    """POST /recurring-profiles/bulk"""

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_pause(self, mock_ctr, client, headers_a):
        profiles = [
            {**STORED_PROFILE, "id": "p1"},
            {**STORED_PROFILE, "id": "p2"},
        ]
        mock_ctr.query_items.side_effect = [[profiles[0]], [profiles[1]]]
        mock_ctr.replace_item.return_value = {}
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "pause", "ids": ["p1", "p2"]},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated"] == 2
        assert data["errors"] == []

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_resume(self, mock_ctr, client, headers_a):
        paused = {**STORED_PROFILE, "status": "Paused"}
        mock_ctr.query_items.return_value = [paused]
        mock_ctr.replace_item.return_value = {}
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "resume", "ids": ["prof-001"]},
            headers=headers_a,
        )
        assert resp.status_code == 200
        body = mock_ctr.replace_item.call_args[1]["body"]
        assert body["status"] == "Active"

    @patch(f"{MODULE}.archive_entity")
    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_delete_archives(self, mock_ctr, mock_archive, client, headers_a):
        mock_ctr.query_items.return_value = [{**STORED_PROFILE}]
        mock_archive.return_value = None
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "delete", "ids": ["prof-001"]},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] == 1
        mock_archive.assert_called_once()

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_invalid_action(self, mock_ctr, client, headers_a):
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "nuke", "ids": ["prof-001"]},
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_empty_ids(self, mock_ctr, client, headers_a):
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "pause", "ids": []},
            headers=headers_a,
        )
        assert resp.status_code == 400

    @patch(f"{MODULE}.recurring_profiles_container")
    def test_bulk_partial_not_found(self, mock_ctr, client, headers_a):
        """IDs that don't exist are counted as errors, not exceptions."""
        mock_ctr.query_items.side_effect = [[], []]  # both not found
        resp = client.post(
            "/api/recurring-profiles/bulk",
            json={"action": "pause", "ids": ["nope1", "nope2"]},
            headers=headers_a,
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["updated"] == 0
        assert len(data["errors"]) == 2
