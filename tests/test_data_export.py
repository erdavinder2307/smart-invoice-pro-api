"""Tests for the full data export (SE-4169): GET /api/export/data and the zip builder. Example data only."""
import csv
import io
import zipfile
from unittest.mock import patch

from tests.conftest import TENANT_A

from smart_invoice_pro.utils.data_export import build_export_zip, payment_rows


CUSTOMER = {
    "id": "cust-001", "display_name": "Asha Traders", "email": "asha@example.com",
    "gst_number": "29ABCDE1234F1Z5", "tenant_id": TENANT_A,
    "portal_password": "hash-not-for-export", "portal_token": "tok-not-for-export",
    "_rid": "x", "_etag": "y", "_ts": 1,
}
INVOICE = {
    "id": "inv-001", "invoice_number": "INV-001", "customer_name": "Asha Traders",
    "total_amount": 1180.0, "status": "Partially Paid", "tenant_id": TENANT_A,
    "items": [{"name": "Widget", "quantity": 2, "rate": 500}],
    "payment_history": [
        {"id": "pay-1", "payment_date": "2026-09-05T10:00:00", "amount": 500, "payment_mode": "UPI", "reference": "R1"},
    ],
}
BILL = {
    "id": "bill-001", "bill_number": "B-77", "vendor_name": "Ravi Supplies",
    "total_amount": 590.0, "status": "Paid", "tenant_id": TENANT_A,
    "payment_history": [
        {"id": "pay-2", "payment_date": "2026-09-01", "amount": 590, "payment_mode": "Bank Transfer"},
    ],
}


def _read_zip(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    return {name: zf.read(name).decode("utf-8-sig") for name in zf.namelist()}


def _csv_rows(text):
    return list(csv.DictReader(io.StringIO(text)))


class TestBuildExportZip:
    def test_contains_one_csv_per_record_type(self):
        files = _read_zip(build_export_zip([CUSTOMER], [INVOICE], [BILL], "2026-10-12T06:00:00Z"))
        assert set(files) == {"customers.csv", "invoices.csv", "bills.csv", "payments.csv", "README.txt"}
        assert [r["id"] for r in _csv_rows(files["customers.csv"])] == ["cust-001"]
        assert [r["invoice_number"] for r in _csv_rows(files["invoices.csv"])] == ["INV-001"]
        assert [r["bill_number"] for r in _csv_rows(files["bills.csv"])] == ["B-77"]

    def test_secrets_and_cosmos_fields_are_left_out(self):
        files = _read_zip(build_export_zip([CUSTOMER], [], [], "t"))
        header = files["customers.csv"].splitlines()[0].split(",")
        for field in ("portal_password", "portal_token", "_rid", "_etag", "_ts"):
            assert field not in header
        assert "not-for-export" not in files["customers.csv"]
        assert header[0] == "id"

    def test_line_items_are_kept_as_json(self):
        files = _read_zip(build_export_zip([], [INVOICE], [], "t"))
        row = _csv_rows(files["invoices.csv"])[0]
        assert '"name": "Widget"' in row["items"]

    def test_csv_starts_with_bom_for_excel(self):
        data = build_export_zip([CUSTOMER], [], [], "t")
        raw = zipfile.ZipFile(io.BytesIO(data)).read("customers.csv")
        assert raw.startswith("﻿".encode("utf-8"))

    def test_empty_tenant_still_gives_all_files(self):
        files = _read_zip(build_export_zip([], [], [], "t"))
        assert files["payments.csv"].splitlines()[0].startswith("direction,payment_id")
        assert "customers.csv: 0 rows" in files["README.txt"]

    def test_formula_like_text_is_escaped_but_numbers_are_not(self):
        customer = {"id": "cust-002", "display_name": "=1+1", "email": "+2+3", "notes": "@SUM(A1)",
                    "city": "-Delhi", "balance": -250.5}
        invoice = dict(INVOICE, payment_history=[
            {"id": "pay-3", "payment_date": "2026-09-06", "amount": -10, "payment_mode": "UPI",
             "reference": "=HYPERLINK(\"http://example.com\")"},
        ])
        files = _read_zip(build_export_zip([customer], [invoice], [], "t"))
        row = _csv_rows(files["customers.csv"])[0]
        assert row["display_name"] == "'=1+1"
        assert row["email"] == "'+2+3"
        assert row["notes"] == "'@SUM(A1)"
        assert row["city"] == "'-Delhi"
        assert row["balance"] == "-250.5"
        payment = _csv_rows(files["payments.csv"])[0]
        assert payment["reference"].startswith("'=HYPERLINK")
        assert payment["amount"] == "-10"

    def test_secret_like_field_names_are_left_out(self):
        customer = dict(CUSTOMER, api_secret="s-not-for-export", login_otp="123456", Reset_Token="r-not-for-export")
        files = _read_zip(build_export_zip([customer], [], [], "t"))
        header = files["customers.csv"].splitlines()[0].split(",")
        for field in ("api_secret", "login_otp", "Reset_Token"):
            assert field not in header
        assert "not-for-export" not in files["customers.csv"]
        assert "123456" not in files["customers.csv"]


class TestPaymentRows:
    def test_received_and_made_sorted_by_date(self):
        rows = payment_rows([INVOICE], [BILL])
        assert [(r["direction"], r["document_number"], r["payment_date"]) for r in rows] == [
            ("made", "B-77", "2026-09-01"),
            ("received", "INV-001", "2026-09-05"),
        ]
        assert rows[1]["party_name"] == "Asha Traders"
        assert rows[0]["party_name"] == "Ravi Supplies"

    def test_documents_without_history_give_no_rows(self):
        assert payment_rows([{"id": "inv-2"}], [{"id": "b-2", "payment_history": None}]) == []


class TestExportEndpoint:
    @patch("smart_invoice_pro.api.data_export_api.log_audit_event")
    @patch("smart_invoice_pro.api.data_export_api.bills_container")
    @patch("smart_invoice_pro.api.data_export_api.invoices_container")
    @patch("smart_invoice_pro.api.data_export_api.customers_container")
    def test_admin_gets_zip_for_own_tenant(self, m_cust, m_inv, m_bill, m_audit, client, headers_a):
        m_cust.query_items.return_value = [CUSTOMER]
        m_inv.query_items.return_value = [INVOICE]
        m_bill.query_items.return_value = [BILL]

        resp = client.get("/api/export/data", headers=headers_a)

        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        assert resp.headers["Content-Disposition"].startswith('attachment; filename="solidev-books-export-')
        assert resp.headers["Cache-Control"] == "no-store"
        files = _read_zip(resp.data)
        assert len(_csv_rows(files["payments.csv"])) == 2
        for m in (m_cust, m_inv, m_bill):
            params = m.query_items.call_args.kwargs["parameters"]
            assert params == [{"name": "@tid", "value": TENANT_A}]
        m_audit.assert_called_once()
        assert m_audit.call_args.args[0]["action"] == "EXPORT"

    @patch("smart_invoice_pro.api.data_export_api.customers_container")
    def test_non_admin_is_refused(self, m_cust, client, headers_a):
        with patch(
            "smart_invoice_pro.utils.permission_checker._get_user_permissions",
            return_value=(False, {"reports": {"view": True}, "customers": {"view": True}}),
        ):
            resp = client.get("/api/export/data", headers=headers_a)
        assert resp.status_code == 403
        m_cust.query_items.assert_not_called()

    def test_needs_login(self, client):
        resp = client.get("/api/export/data")
        assert resp.status_code == 401

    @patch("smart_invoice_pro.api.data_export_api.log_audit_event")
    @patch("smart_invoice_pro.api.data_export_api.customers_container")
    def test_query_failure_gives_500_without_details(self, m_cust, m_audit, client, headers_a):
        m_cust.query_items.side_effect = RuntimeError("connection string leaked here")
        resp = client.get("/api/export/data", headers=headers_a)
        assert resp.status_code == 500
        assert "leaked" not in resp.get_data(as_text=True)
        m_audit.assert_not_called()
