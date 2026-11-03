"""
SE-4196: the GST tax summary and the sales summary count every issued invoice
(Issued, Overdue, Partially Paid, Paid) and leave out Draft and Cancelled.
"Pending" is not an invoice status.
"""
import re
from unittest.mock import patch

from tests.conftest import TENANT_A

STATUSES = ["Draft", "Issued", "Overdue", "Partially Paid", "Paid", "Cancelled"]


def _invoice(n, status):
    # One line: 1 x 1000 at 18% → taxable 1000, tax 180, total 1180
    return {
        "id": f"inv-{n}", "tenant_id": TENANT_A, "status": status,
        "customer_id": "cust-1", "customer_name": "Example Customer",
        "issue_date": "2026-10-05", "total_amount": 1180.0,
        "amount_paid": 1180.0 if status == "Paid" else (500.0 if status == "Partially Paid" else 0.0),
        "igst_amount": 0,
        "items": [{"quantity": 1, "rate": 1000, "discount": 0, "tax": 18}],
    }


INVOICES = [_invoice(i, s) for i, s in enumerate(STATUSES)]


def _query_honouring_status_filter(query=None, **_):
    """Fake Cosmos: return the invoices whose status the query's IN (...) list names."""
    m = re.search(r"c\.status IN \(([^)]*)\)", query or "")
    wanted = set(re.findall(r"'([^']*)'", m.group(1))) if m else set(STATUSES)
    return [inv for inv in INVOICES if inv["status"] in wanted]


def _get(client, headers, path):
    with patch("smart_invoice_pro.api.reports_api.invoices_container") as mock_inv:
        mock_inv.query_items.side_effect = _query_honouring_status_filter
        resp = client.get(f"{path}?start_date=2026-10-01&end_date=2026-10-31", headers=headers)
        query = mock_inv.query_items.call_args.kwargs["query"]
    assert resp.status_code == 200
    return resp.get_json(), query


def test_gst_tax_summary_counts_issued_overdue_partly_paid_and_paid(client, headers_a):
    data, query = _get(client, headers_a, "/api/reports/gst-tax-summary")
    assert "'Pending'" not in query
    totals = data["totals"]
    assert totals["invoice_count"] == 4
    assert totals["taxable_value"] == 4000.0
    assert totals["total_tax"] == 720.0
    assert totals["cgst"] == 360.0 and totals["sgst"] == 360.0
    assert data["tax_breakdown"] == [{
        "tax_rate": "18%", "taxable_value": 4000.0,
        "cgst": 360.0, "sgst": 360.0, "igst": 0.0, "total_tax": 720.0,
    }]


def test_sales_summary_counts_issued_overdue_partly_paid_and_paid(client, headers_a):
    data, query = _get(client, headers_a, "/api/reports/sales-summary")
    assert "'Pending'" not in query
    assert data["invoice_count"] == 4
    assert data["total_revenue"] == 4720.0
    assert data["total_paid"] == 1680.0  # 1180 paid + 500 part-paid
    assert data["customer_summary"][0]["invoice_count"] == 4
    assert data["monthly_breakdown"] == [{"month": "2026-10", "total": 4720.0}]
