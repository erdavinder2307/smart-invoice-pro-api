"""Tests for cron_jobs.py – low-stock check and schedule-info."""
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import TENANT_A, USER_A


PRODUCT_A = {
    "id": "prod-1",
    "name": "Widget",
    "category": "Parts",
    "unit": "Nos",
    "reorder_level": 10,
    "reorder_qty": 50,
    "preferred_vendor_id": "vendor-1",
    "tenant_id": TENANT_A,
}

PRODUCT_B = {
    "id": "prod-2",
    "name": "Gadget",
    "category": "Parts",
    "unit": "Nos",
    "reorder_level": 5,
    "reorder_qty": 20,
    "preferred_vendor_id": "",
    "tenant_id": TENANT_A,
}

PRODUCT_NO_REORDER = {
    "id": "prod-3",
    "name": "Service Item",
    "category": "Services",
    "unit": "Hrs",
    "reorder_level": 0,
    "reorder_qty": 0,
    "tenant_id": TENANT_A,
}


def _mock_get_container(name, pk):
    """Return a MagicMock container seeded per container name."""
    m = MagicMock()
    m.read_all_items.return_value = []
    m.query_items.return_value = []
    return m


class TestCheckLowStock:
    """GET/POST /cron/check-low-stock"""

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_low_stock_detected(self, mock_gc, mock_notif, client, headers_a):
        products_ctr = MagicMock()
        stock_ctr = MagicMock()

        products_ctr.read_all_items.return_value = [PRODUCT_A, PRODUCT_B]
        stock_ctr.read_all_items.return_value = [
            {"product_id": "prod-1", "quantity": 5, "type": "IN"},
            {"product_id": "prod-2", "quantity": 100, "type": "IN"},
        ]

        def gc_side(name, pk):
            return products_ctr if name == "products" else stock_ctr

        mock_gc.side_effect = gc_side

        resp = client.get("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["low_stock_count"] == 1
        assert data["products"][0]["name"] == "Widget"
        assert data["email_sent"] is False
        mock_notif.assert_called_once()

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_no_low_stock(self, mock_gc, mock_notif, client, headers_a):
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = [PRODUCT_A]
        stock_ctr.read_all_items.return_value = [
            {"product_id": "prod-1", "quantity": 100, "type": "IN"},
        ]

        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.get("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["low_stock_count"] == 0
        mock_notif.assert_not_called()

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_stock_calculation_in_out(self, mock_gc, mock_notif, client, headers_a):
        """IN - OUT gives current stock. 20 IN - 15 OUT = 5, reorder=10 → low."""
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = [PRODUCT_A]
        stock_ctr.read_all_items.return_value = [
            {"product_id": "prod-1", "quantity": 20, "type": "IN"},
            {"product_id": "prod-1", "quantity": 15, "type": "OUT"},
        ]
        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.get("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["low_stock_count"] == 1
        assert data["products"][0]["current_stock"] == 5.0

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_reorder_level_zero_skipped(self, mock_gc, mock_notif, client, headers_a):
        """Products with reorder_level=0 are never flagged."""
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = [PRODUCT_NO_REORDER]
        stock_ctr.read_all_items.return_value = []
        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.get("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["low_stock_count"] == 0

    @patch("smart_invoice_pro.api.cron_jobs.send_low_stock_email", return_value=True)
    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_email_sent_when_enabled(self, mock_gc, mock_notif, mock_email, client, headers_a):
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = [PRODUCT_A]
        stock_ctr.read_all_items.return_value = []
        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.get("/api/cron/check-low-stock?send_email=true", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["email_sent"] is True
        mock_email.assert_called_once()

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_post_method_also_works(self, mock_gc, mock_notif, client, headers_a):
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = []
        stock_ctr.read_all_items.return_value = []
        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.post("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.get_container")
    def test_product_without_tenant_no_notification(self, mock_gc, mock_notif, client, headers_a):
        prod = {**PRODUCT_A, "tenant_id": None}
        products_ctr = MagicMock()
        stock_ctr = MagicMock()
        products_ctr.read_all_items.return_value = [prod]
        stock_ctr.read_all_items.return_value = []
        mock_gc.side_effect = lambda n, pk: products_ctr if n == "products" else stock_ctr

        resp = client.get("/api/cron/check-low-stock?send_email=false", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["low_stock_count"] == 1
        mock_notif.assert_not_called()


class TestSendLowStockEmail:
    """Unit tests for send_low_stock_email()."""

    @patch("smart_invoice_pro.api.cron_jobs.CONNECTION_STRING", None)
    def test_no_connection_string(self):
        from smart_invoice_pro.api.cron_jobs import send_low_stock_email
        assert send_low_stock_email([PRODUCT_A]) is False

    @patch("smart_invoice_pro.api.cron_jobs.CONNECTION_STRING", "endpoint=https://x.com;accesskey=KEY")
    @patch("smart_invoice_pro.api.cron_jobs.EmailClient")
    def test_email_sent_successfully(self, mock_cls):
        mock_client = MagicMock()
        mock_poller = MagicMock()
        mock_poller.result.return_value = {"id": "msg-123"}
        mock_client.begin_send.return_value = mock_poller
        mock_cls.from_connection_string.return_value = mock_client

        low_stock = {
            "id": "prod-1", "name": "Widget", "current_stock": 5,
            "reorder_level": 10, "reorder_qty": 50, "unit": "Nos",
        }
        from smart_invoice_pro.api.cron_jobs import send_low_stock_email
        assert send_low_stock_email([low_stock]) is True
        mock_client.begin_send.assert_called_once()

    @patch("smart_invoice_pro.api.cron_jobs.CONNECTION_STRING", "endpoint=https://x.com;accesskey=KEY")
    @patch("smart_invoice_pro.api.cron_jobs.EmailClient")
    def test_email_send_failure(self, mock_cls):
        mock_cls.from_connection_string.side_effect = Exception("Cannot connect")

        low_stock = {
            "id": "prod-1", "name": "Widget", "current_stock": 5,
            "reorder_level": 10, "reorder_qty": 50, "unit": "Nos",
        }
        from smart_invoice_pro.api.cron_jobs import send_low_stock_email
        assert send_low_stock_email([low_stock]) is False


class TestScheduleInfo:
    """GET /cron/schedule-info"""

    def test_returns_schedule(self, client, headers_a):
        resp = client.get("/api/cron/schedule-info", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "jobs" in data
        assert len(data["jobs"]) >= 2
        names = [j["name"] for j in data["jobs"]]
        assert "Low Stock Check" in names
        assert "Generate Recurring Invoices" in names


# ── BUG-002 fix: recurring invoice generation ─────────────────────────────────

SAMPLE_PROFILE = {
    "id": "prof-001",
    "tenant_id": TENANT_A,
    "customer_id": "cust-001",
    "customer_name": "ACME Corp",
    "profile_name": "Monthly Maintenance",
    "frequency": "Monthly",
    "recurrence_rule": {},
    "next_run_date": "2024-01-01",   # past date → due today
    "last_run_date": None,
    "status": "Active",
    "auto_send": False,
    "occurrences_created": 0,
    "occurrence_limit": None,
    "ends_type": "never",
    "end_date": None,
    "items": [
        {"name": "Maintenance", "quantity": 1, "unit_price": 500.0, "amount": 500.0}
    ],
    "subtotal": 500.0,
    "cgst_amount": 45.0,
    "sgst_amount": 45.0,
    "igst_amount": 0.0,
    "notes": "",
    "terms_conditions": "",
    "is_gst_applicable": True,
    "payment_terms": "Net 30",
}


class TestGenerateRecurringInvoices:
    """POST /cron/generate-recurring"""

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.log_audit")
    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_generates_invoice_for_due_profile(
        self, mock_rp, mock_inv, mock_audit, mock_notif, client, headers_a
    ):
        """A due Active profile produces one Draft invoice."""
        mock_rp.query_items.return_value = [SAMPLE_PROFILE.copy()]

        with patch("smart_invoice_pro.api.recurring_profiles_api.calculate_next_run_date", return_value="2024-02-01"), \
             patch("smart_invoice_pro.api.invoice_preferences_api.generate_invoice_number", return_value="INV-00042"):
            resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["generated_count"] == 1
        assert data["error_count"] == 0
        mock_inv.create_item.assert_called_once()
        created_inv = mock_inv.create_item.call_args[1]["body"]
        assert created_inv["invoice_number"] == "INV-00042"
        assert created_inv["status"] == "Draft"
        assert created_inv["tenant_id"] == TENANT_A
        assert created_inv["customer_id"] == "cust-001"
        assert created_inv["total_amount"] == 590.0   # 500 + 45 + 45

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.log_audit")
    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_auto_send_true_creates_issued_invoice(
        self, mock_rp, mock_inv, mock_audit, mock_notif, client, headers_a
    ):
        """Profile with auto_send=True generates an Issued invoice."""
        profile = {**SAMPLE_PROFILE, "auto_send": True}
        mock_rp.query_items.return_value = [profile]

        with patch("smart_invoice_pro.api.recurring_profiles_api.calculate_next_run_date", return_value="2024-02-01"), \
             patch("smart_invoice_pro.api.invoice_preferences_api.generate_invoice_number", return_value="INV-00043"):
            resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        created_inv = mock_inv.create_item.call_args[1]["body"]
        assert created_inv["status"] == "Issued"

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.log_audit")
    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_profile_next_run_date_advances(
        self, mock_rp, mock_inv, mock_audit, mock_notif, client, headers_a
    ):
        """Profile's next_run_date is updated to the next calculated date."""
        profile = {**SAMPLE_PROFILE.copy()}
        mock_rp.query_items.return_value = [profile]

        with patch("smart_invoice_pro.api.recurring_profiles_api.calculate_next_run_date", return_value="2024-02-15"), \
             patch("smart_invoice_pro.api.invoice_preferences_api.generate_invoice_number", return_value="INV-00044"):
            resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        updated_profile = mock_rp.replace_item.call_args[1]["body"]
        assert updated_profile["next_run_date"] == "2024-02-15"
        assert updated_profile["occurrences_created"] == 1

    @patch("smart_invoice_pro.api.cron_jobs.create_notification")
    @patch("smart_invoice_pro.api.cron_jobs.log_audit")
    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_marks_completed_when_occurrence_limit_reached(
        self, mock_rp, mock_inv, mock_audit, mock_notif, client, headers_a
    ):
        """Profile reaching occurrence_limit is marked Completed after generating final invoice."""
        profile = {
            **SAMPLE_PROFILE,
            "ends_type": "after_occurrences",
            "occurrence_limit": 3,
            "occurrences_created": 2,   # this will be the 3rd (final) occurrence
        }
        mock_rp.query_items.return_value = [profile]

        with patch("smart_invoice_pro.api.recurring_profiles_api.calculate_next_run_date", return_value="2024-02-01"), \
             patch("smart_invoice_pro.api.invoice_preferences_api.generate_invoice_number", return_value="INV-00045"):
            resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        assert resp.get_json()["generated_count"] == 1
        updated_profile = mock_rp.replace_item.call_args[1]["body"]
        assert updated_profile["status"] == "Completed"
        assert updated_profile["occurrences_created"] == 3

    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_skips_profile_already_at_limit(self, mock_rp, mock_inv, client, headers_a):
        """Profile already at occurrence_limit is marked Completed without creating invoice."""
        profile = {
            **SAMPLE_PROFILE,
            "ends_type": "after_occurrences",
            "occurrence_limit": 3,
            "occurrences_created": 3,   # already reached
        }
        mock_rp.query_items.return_value = [profile]

        resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        assert resp.get_json()["generated_count"] == 0
        mock_inv.create_item.assert_not_called()
        updated_profile = mock_rp.replace_item.call_args[1]["body"]
        assert updated_profile["status"] == "Completed"

    @patch("smart_invoice_pro.api.cron_jobs.invoices_container")
    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_skips_profile_past_end_date(self, mock_rp, mock_inv, client, headers_a):
        """Profile with ends_type=on_date and expired end_date is marked Completed, no invoice."""
        profile = {
            **SAMPLE_PROFILE,
            "ends_type": "on_date",
            "end_date": "2023-12-31",   # expired
        }
        mock_rp.query_items.return_value = [profile]

        resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        assert resp.get_json()["generated_count"] == 0
        mock_inv.create_item.assert_not_called()
        updated_profile = mock_rp.replace_item.call_args[1]["body"]
        assert updated_profile["status"] == "Completed"

    @patch("smart_invoice_pro.api.cron_jobs.recurring_profiles_container")
    def test_no_due_profiles_returns_zero(self, mock_rp, client, headers_a):
        """When no profiles are due, generated_count is 0 with no errors."""
        mock_rp.query_items.return_value = []

        resp = client.post("/api/cron/generate-recurring", headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["generated_count"] == 0
        assert data["error_count"] == 0
