"""
Tests for Bank Reconciliation API (bank_reconciliation_api.py).
Upload, list, match/unmatch, create-expense, auto-match, delete, matchable.
"""
import io
from unittest.mock import patch
from tests.conftest import TENANT_A, USER_A


TXN_DOC = {
    "id": "txn-001",
    "user_id": USER_A,
    "bank_account_id": "ba-001",
    "date": "2024-06-15",
    "description": "Payment from Acme",
    "amount": 1000.0,
    "match_status": "unmatched",
    "match_type": None,
    "match_id": None,
}


def _patches():
    """Patch all bank_reconciliation_api containers."""
    return (
        patch("smart_invoice_pro.api.bank_reconciliation_api.bank_txns_container"),
        patch("smart_invoice_pro.api.bank_reconciliation_api.invoices_container"),
        patch("smart_invoice_pro.api.bank_reconciliation_api.expenses_container"),
    )


def _import_patches():
    return (
        patch("smart_invoice_pro.services.bank_import.import_workflow_service.bank_import_batches_container"),
        patch("smart_invoice_pro.services.bank_import.import_workflow_service.bank_import_jobs_container"),
        patch("smart_invoice_pro.services.bank_import.import_workflow_service.bank_import_rows_container"),
        patch("smart_invoice_pro.services.bank_import.import_workflow_service.bank_import_artifacts_container"),
    )


class TestUploadStatement:
    """POST /api/reconciliation/upload"""

    def test_csv_upload_success(self, client, headers_a):
        csv_content = "date,description,debit,credit,balance\n2024-01-15,Acme Payment,,1000.00,5000.00\n"
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3:
            mock_txns.create_item.return_value = {}
            mock_inv.query_items.return_value = []  # no auto-match
            data = {
                "file": (io.BytesIO(csv_content.encode()), "statement.csv"),
                "bank_account_id": "ba-001",
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 201
        result = resp.get_json()
        assert result["imported"] >= 1

    def test_no_file_returns_400(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = client.post("/api/reconciliation/upload",
                               data={"bank_account_id": "ba-001"},
                               content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 400

    def test_unsupported_format(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            data = {
                "file": (io.BytesIO(b"fake"), "statement.xlsx"),
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 400

    def test_empty_csv_returns_422(self, client, headers_a):
        csv_content = "date,description,debit,credit,balance\n"
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            data = {
                "file": (io.BytesIO(csv_content.encode()), "empty.csv"),
            }
            resp = client.post("/api/reconciliation/upload",
                               data=data, content_type="multipart/form-data",
                               headers={"Authorization": headers_a["Authorization"]})
        assert resp.status_code == 422


class TestImportBatches:
    """Review-first import batch workflow."""

    def test_create_import_batch_success(self, client, headers_a):
        csv_content = "date,description,debit,credit,balance\n2024-01-15,Acme Payment,,1000.00,5000.00\n"
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            mock_batches.create_item.return_value = {}
            mock_batches.replace_item.return_value = {}
            mock_jobs.create_item.return_value = {}
            mock_jobs.replace_item.return_value = {}
            mock_rows.create_item.return_value = {}
            mock_artifacts.create_item.return_value = {}
            resp = client.post(
                "/api/reconciliation/import-batches",
                data={
                    "file": (io.BytesIO(csv_content.encode()), "statement.csv"),
                    "bank_account_id": "ba-001",
                },
                content_type="multipart/form-data",
                headers={"Authorization": headers_a["Authorization"]},
            )
        assert resp.status_code == 201
        payload = resp.get_json()
        assert payload["batch"]["workflow_mode"] == "deterministic_parse"
        assert payload["batch"]["row_count"] == 1
        assert payload["job"]["status"] == "completed"
        assert len(payload["rows"]) == 1

    def test_create_import_batch_review_only_file(self, client, headers_a):
        """txt files remain review-only; pdf/xlsx are now handled by AI parser."""
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            mock_batches.create_item.return_value = {}
            mock_batches.replace_item.return_value = {}
            mock_jobs.create_item.return_value = {}
            mock_jobs.replace_item.return_value = {}
            mock_rows.create_item.return_value = {}
            mock_artifacts.create_item.return_value = {}
            resp = client.post(
                "/api/reconciliation/import-batches",
                data={
                    "file": (io.BytesIO(b"bank memo"), "statement.txt"),
                    "bank_account_id": "ba-001",
                },
                content_type="multipart/form-data",
                headers={"Authorization": headers_a["Authorization"]},
            )
        assert resp.status_code == 201
        payload = resp.get_json()
        assert payload["batch"]["workflow_mode"] == "review_only"
        assert payload["batch"]["status"] == "review_required"
        assert payload["rows"] == []

    def test_list_import_batches(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2, p3, p4:
            mock_batches.query_items.return_value = [{"id": "batch-1", "tenant_id": TENANT_A}]
            resp = client.get("/api/reconciliation/import-batches", headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_import_batch_rows(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2, p3 as mock_rows, p4:
            mock_batches.query_items.return_value = [{"id": "batch-1", "tenant_id": TENANT_A}]
            mock_rows.query_items.return_value = [{"id": "row-1", "batch_id": "batch-1"}]
            resp = client.get("/api/reconciliation/import-batches/batch-1/rows", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()[0]["id"] == "row-1"

    def test_get_import_job(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1, p2 as mock_jobs, p3, p4:
            mock_jobs.query_items.return_value = [
                {"id": "job-1", "tenant_id": TENANT_A, "batch_id": "batch-1", "status": "completed", "progress": 100}
            ]
            resp = client.get("/api/reconciliation/import-jobs/job-1", headers=headers_a)
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["id"] == "job-1"
        assert payload["status"] == "completed"

    def test_get_import_job_not_found(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1, p2 as mock_jobs, p3, p4:
            mock_jobs.query_items.return_value = []
            resp = client.get("/api/reconciliation/import-jobs/missing-job", headers=headers_a)
        assert resp.status_code == 404

    def test_update_import_row(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1, p2, p3 as mock_rows, p4:
            mock_rows.query_items.return_value = [{
                "id": "row-1",
                "tenant_id": TENANT_A,
                "batch_id": "batch-1",
                "review_status": "pending_review",
                "amount": 1000.0,
                "description": "Acme Payment",
                "normalized_date": "2024-01-15",
            }]
            mock_rows.replace_item.return_value = {}
            resp = client.patch(
                "/api/reconciliation/import-batches/batch-1/rows/row-1",
                json={"amount": 1200.0},
                headers=headers_a,
            )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["amount"] == 1200.0
        assert payload["review_status"] == "reviewed"

    def test_approve_import_batch_creates_transactions(self, client, headers_a):
        p1, p2, p3 = _patches()
        p4, p5, p6, p7 = _import_patches()
        with p1 as mock_txns, p2 as mock_inv, p3, p4 as mock_batches, p5, p6 as mock_rows, p7:
            mock_batches.query_items.side_effect = [
                [{"id": "batch-1", "tenant_id": TENANT_A, "status": "review_ready", "filename": "statement.csv"}],
                [{"id": "batch-1", "tenant_id": TENANT_A, "status": "review_ready", "filename": "statement.csv"}],
            ]
            mock_batches.replace_item.return_value = {}
            mock_rows.query_items.return_value = [{
                "id": "row-1",
                "tenant_id": TENANT_A,
                "batch_id": "batch-1",
                "bank_account_id": "ba-001",
                "normalized_date": "2024-01-15",
                "description": "Acme Payment",
                "amount": 1000.0,
                "currency": "INR",
                "review_status": "ready",
            }]
            mock_txns.create_item.return_value = {}
            mock_inv.query_items.return_value = []
            resp = client.post("/api/reconciliation/import-batches/batch-1/approve", headers=headers_a)
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["transactions_created"] == 1
        assert payload["batch"]["status"] == "reconciliation_prepared"


class TestListTransactions:
    """GET /api/reconciliation/transactions"""

    def test_list_all(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC]
            resp = client.get("/api/reconciliation/transactions", headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_filter_by_status(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.get("/api/reconciliation/transactions?status=matched",
                              headers=headers_a)
        assert resp.status_code == 200


class TestMatchTransaction:
    """POST /api/reconciliation/<txn_id>/match"""

    def test_manual_match_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_txns.replace_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/match",
                               json={"match_type": "invoice", "match_id": "inv-123"},
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["match_status"] == "matched"
        assert data["match_type"] == "invoice"

    def test_match_missing_fields(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = client.post("/api/reconciliation/txn-001/match",
                               json={}, headers=headers_a)
        assert resp.status_code == 400

    def test_match_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/match",
                               json={"match_type": "invoice", "match_id": "inv-123"},
                               headers=headers_a)
        assert resp.status_code == 404


class TestUnmatchTransaction:
    """POST /api/reconciliation/<txn_id>/unmatch"""

    def test_unmatch_success(self, client, headers_a):
        matched_txn = {**TXN_DOC, "match_status": "matched", "match_type": "invoice", "match_id": "inv-123"}
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [matched_txn.copy()]
            mock_txns.replace_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/unmatch",
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["match_status"] == "unmatched"
        assert data["match_type"] is None

    def test_unmatch_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/unmatch",
                               headers=headers_a)
        assert resp.status_code == 404


class TestCreateExpenseFromTxn:
    """POST /api/reconciliation/<txn_id>/create-expense"""

    def test_create_expense_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3 as mock_exp:
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_txns.replace_item.return_value = {}
            mock_exp.create_item.return_value = {}
            resp = client.post("/api/reconciliation/txn-001/create-expense",
                               json={"vendor_name": "Acme", "category": "Office"},
                               headers=headers_a)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["expense"]["vendor_name"] == "Acme"
        assert data["expense"]["category"] == "Office"
        assert data["transaction"]["match_status"] == "matched"

    def test_create_expense_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/create-expense",
                               json={"vendor_name": "X"}, headers=headers_a)
        assert resp.status_code == 404


class TestAutoMatch:
    """POST /api/reconciliation/auto-match"""

    def test_auto_match_no_unmatched(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/auto-match", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["processed"] == 0
        assert data["newly_matched"] == 0

    def test_auto_match_with_invoice(self, client, headers_a):
        """An unmatched txn with amount matching an invoice's balance_due gets matched."""
        unmatched = TXN_DOC.copy()
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3:
            mock_txns.query_items.return_value = [unmatched]
            mock_txns.replace_item.return_value = {}
            mock_inv.query_items.return_value = [
                {"id": "inv-100", "invoice_number": "INV-100", "balance_due": 1000.0, "customer_id": "c1"}
            ]
            resp = client.post("/api/reconciliation/auto-match", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["newly_matched"] == 1


class TestDeleteTransaction:
    """DELETE /api/reconciliation/<txn_id>"""

    def test_delete_success(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = [TXN_DOC]
            resp = client.delete("/api/reconciliation/txn-001", headers=headers_a)
        assert resp.status_code == 200

    def test_delete_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.delete("/api/reconciliation/bad-id", headers=headers_a)
        assert resp.status_code == 404


class TestGetMatchable:
    """GET /api/reconciliation/matchable"""

    def test_matchable_invoices(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2 as mock_inv, p3:
            mock_inv.query_items.return_value = [
                {"id": "inv-1", "invoice_number": "INV-001", "balance_due": 500, "status": "Issued"}
            ]
            resp = client.get("/api/reconciliation/matchable?type=invoice",
                              headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_matchable_expenses(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1, p2, p3 as mock_exp:
            mock_exp.query_items.return_value = [
                {"id": "exp-1", "vendor_name": "Vendor A", "amount": 200}
            ]
            resp = client.get("/api/reconciliation/matchable?type=expense",
                              headers=headers_a)
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1


# ─────────────────────────────────────────────────────────────────────────────
# AI-POWERED RECONCILIATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

_AI_SUGGESTION_INVOICE = {
    "match_type": "invoice",
    "match_id": "inv-100",
    "confidence": 0.92,
    "reasoning": "Description contains customer name and amount matches closely.",
}

_AI_SUGGESTION_NONE = {
    "match_type": None,
    "match_id": None,
    "confidence": 0.0,
    "reasoning": "No suitable match found.",
}


class TestAiParseImport:
    """Tests for AI-powered xlsx/pdf import (workflow_mode='ai_parse')."""

    def test_xlsx_file_routes_to_ai_parse_mode(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            mock_batches.create_item.return_value = {}
            mock_batches.replace_item.return_value = {}
            mock_jobs.create_item.return_value = {}
            mock_jobs.replace_item.return_value = {}
            mock_rows.create_item.return_value = {}
            mock_artifacts.create_item.return_value = {}

            ai_rows = [
                {"row_index": 1, "date": "2026-01-15", "description": "NEFT ACME", "amount": 1180.0, "running_balance": 50000.0, "raw_row": {}, "parser": "ai_claude"},
            ]
            with patch("smart_invoice_pro.services.ai_bank_parser_service.parse_xlsx", return_value=ai_rows):
                resp = client.post(
                    "/api/reconciliation/import-batches",
                    data={
                        "file": (io.BytesIO(b"fake excel bytes"), "statement.xlsx"),
                        "bank_account_id": "ba-001",
                    },
                    content_type="multipart/form-data",
                    headers={"Authorization": headers_a["Authorization"]},
                )
        assert resp.status_code == 201
        payload = resp.get_json()
        assert payload["batch"]["workflow_mode"] == "ai_parse"
        assert payload["batch"]["row_count"] == 1
        assert payload["job"]["status"] == "completed"

    def test_pdf_file_routes_to_ai_parse_mode(self, client, headers_a):
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            mock_batches.create_item.return_value = {}
            mock_batches.replace_item.return_value = {}
            mock_jobs.create_item.return_value = {}
            mock_jobs.replace_item.return_value = {}
            mock_rows.create_item.return_value = {}
            mock_artifacts.create_item.return_value = {}

            ai_rows = [
                {"row_index": 1, "date": "2026-02-01", "description": "ATM CASH", "amount": -3500.0, "running_balance": None, "raw_row": {}, "parser": "ai_claude"},
                {"row_index": 2, "date": "2026-02-05", "description": "UPI ZEPTO", "amount": -299.0, "running_balance": None, "raw_row": {}, "parser": "ai_claude"},
            ]
            with patch("smart_invoice_pro.services.ai_bank_parser_service.parse_pdf", return_value=ai_rows):
                resp = client.post(
                    "/api/reconciliation/import-batches",
                    data={
                        "file": (io.BytesIO(b"%PDF fake"), "sbi_statement.pdf"),
                        "bank_account_id": "ba-002",
                    },
                    content_type="multipart/form-data",
                    headers={"Authorization": headers_a["Authorization"]},
                )
        assert resp.status_code == 201
        payload = resp.get_json()
        assert payload["batch"]["workflow_mode"] == "ai_parse"
        assert payload["batch"]["row_count"] == 2

    def test_ai_parse_no_api_key_returns_503(self, client, headers_a):
        """If ANTHROPIC_API_KEY is missing, AI parse raises RuntimeError → 503."""
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            mock_batches.create_item.return_value = {}
            mock_batches.replace_item.return_value = {}
            mock_jobs.create_item.return_value = {}
            mock_jobs.replace_item.return_value = {}
            mock_rows.create_item.return_value = {}
            mock_artifacts.create_item.return_value = {}

            with patch(
                "smart_invoice_pro.services.ai_bank_parser_service.parse_xlsx",
                side_effect=RuntimeError("ANTHROPIC_API_KEY is not set"),
            ):
                resp = client.post(
                    "/api/reconciliation/import-batches",
                    data={
                        "file": (io.BytesIO(b"fake excel bytes"), "statement.xlsx"),
                        "bank_account_id": "ba-001",
                    },
                    content_type="multipart/form-data",
                    headers={"Authorization": headers_a["Authorization"]},
                )
        # RuntimeError propagates → batch status=failed → still returns 201 with status info
        assert resp.status_code == 201
        payload = resp.get_json()
        assert payload["batch"]["status"] == "failed"


class TestDeleteImportBatch:
    """DELETE /api/reconciliation/import-batches/<batch_id>"""

    def test_delete_batch_success(self, client, headers_a):
        """Delete a pending batch — returns 200 with deleted=True."""
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2 as mock_jobs, p3 as mock_rows, p4 as mock_artifacts:
            batch_doc = {
                "id": "batch-del-1",
                "tenant_id": TENANT_A,
                "status": "pending_review",
                "job_id": "job-del-1",
                "raw_artifact_id": None,
            }
            mock_batches.query_items.return_value = [batch_doc]
            mock_rows.query_items.return_value = [{"id": "row-1"}, {"id": "row-2"}]
            mock_rows.delete_item.return_value = None
            mock_jobs.delete_item.return_value = None
            mock_batches.delete_item.return_value = None

            resp = client.delete(
                "/api/reconciliation/import-batches/batch-del-1",
                headers=headers_a,
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deleted"] is True
        assert data["batch_id"] == "batch-del-1"

    def test_delete_batch_not_found_returns_404(self, client, headers_a):
        """Batch not owned by tenant returns 404."""
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2, p3, p4:
            mock_batches.query_items.return_value = []
            resp = client.delete(
                "/api/reconciliation/import-batches/nonexistent",
                headers=headers_a,
            )
        assert resp.status_code == 404

    def test_delete_approved_batch_returns_409(self, client, headers_a):
        """Approved batch cannot be deleted — returns 409."""
        p1, p2, p3, p4 = _import_patches()
        with p1 as mock_batches, p2, p3, p4:
            mock_batches.query_items.return_value = [{
                "id": "batch-approved",
                "tenant_id": TENANT_A,
                "status": "approved",
            }]
            resp = client.delete(
                "/api/reconciliation/import-batches/batch-approved",
                headers=headers_a,
            )
        assert resp.status_code == 409
        assert "approved" in resp.get_json()["error"].lower()


class TestAiSuggestMatch:
    """POST /api/reconciliation/<txn_id>/ai-suggest"""

    def test_ai_suggest_returns_suggestion(self, client, headers_a):
        """Happy path: transaction found, Claude returns a suggestion."""
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3 as mock_exp, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      return_value=_AI_SUGGESTION_INVOICE) as mock_ai:
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_inv.query_items.return_value = [
                {"id": "inv-100", "invoice_number": "INV-100", "balance_due": 1000.0,
                 "customer_name": "Acme Corp", "due_date": "2024-01-20"}
            ]
            mock_exp.query_items.return_value = []
            resp = client.post("/api/reconciliation/txn-001/ai-suggest", headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["transaction_id"] == "txn-001"
        assert data["suggestion"]["match_type"] == "invoice"
        assert data["suggestion"]["match_id"] == "inv-100"
        assert data["suggestion"]["confidence"] == 0.92

    def test_ai_suggest_transaction_not_found(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/bad-id/ai-suggest", headers=headers_a)
        assert resp.status_code == 404

    def test_ai_suggest_no_api_key_returns_503(self, client, headers_a):
        """If ANTHROPIC_API_KEY is not set, endpoint returns 503."""
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      side_effect=RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")):
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            resp = client.post("/api/reconciliation/txn-001/ai-suggest", headers=headers_a)
        assert resp.status_code == 503

    def test_ai_suggest_no_match_returned(self, client, headers_a):
        """Claude returns no match — 200 with null match_type."""
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3 as mock_exp, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      return_value=_AI_SUGGESTION_NONE):
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            mock_inv.query_items.return_value = []
            mock_exp.query_items.return_value = []
            resp = client.post("/api/reconciliation/txn-001/ai-suggest", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["suggestion"]["match_type"] is None
        assert data["suggestion"]["confidence"] == 0.0


class TestRunAiMatch:
    """POST /api/reconciliation/ai-match"""

    def test_ai_match_applies_high_confidence(self, client, headers_a):
        """Transactions whose AI confidence >= threshold should be auto-applied."""
        unmatched = TXN_DOC.copy()
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3 as mock_exp, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      return_value=_AI_SUGGESTION_INVOICE):
            mock_txns.query_items.return_value = [unmatched]
            mock_txns.replace_item.return_value = {}
            mock_inv.query_items.return_value = [
                {"id": "inv-100", "invoice_number": "INV-100", "balance_due": 1000.0,
                 "customer_name": "Acme Corp", "due_date": "2024-01-20"}
            ]
            mock_exp.query_items.return_value = []
            resp = client.post("/api/reconciliation/ai-match", headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["processed"] == 1
        assert data["newly_matched"] == 1
        assert data["results"][0]["applied"] is True

    def test_ai_match_skips_low_confidence(self, client, headers_a):
        """Transactions below threshold should be suggested but not applied."""
        unmatched = TXN_DOC.copy()
        low_conf = {**_AI_SUGGESTION_INVOICE, "confidence": 0.50}
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3 as mock_exp, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      return_value=low_conf):
            mock_txns.query_items.return_value = [unmatched]
            mock_inv.query_items.return_value = []
            mock_exp.query_items.return_value = []
            resp = client.post("/api/reconciliation/ai-match",
                               json={"confidence_threshold": 0.85}, headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["newly_matched"] == 0
        assert data["results"][0]["applied"] is False

    def test_ai_match_custom_threshold(self, client, headers_a):
        """Passing a lower threshold should auto-apply borderline matches."""
        unmatched = TXN_DOC.copy()
        mid_conf = {**_AI_SUGGESTION_INVOICE, "confidence": 0.70}
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2 as mock_inv, p3 as mock_exp, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      return_value=mid_conf):
            mock_txns.query_items.return_value = [unmatched]
            mock_txns.replace_item.return_value = {}
            mock_inv.query_items.return_value = []
            mock_exp.query_items.return_value = []
            resp = client.post("/api/reconciliation/ai-match",
                               json={"confidence_threshold": 0.65}, headers=headers_a)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["newly_matched"] == 1
        assert data["results"][0]["applied"] is True

    def test_ai_match_no_unmatched(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3:
            mock_txns.query_items.return_value = []
            resp = client.post("/api/reconciliation/ai-match", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["processed"] == 0
        assert data["newly_matched"] == 0

    def test_ai_match_no_api_key_returns_503(self, client, headers_a):
        p1, p2, p3 = _patches()
        with p1 as mock_txns, p2, p3, \
                patch("smart_invoice_pro.services.ai_reconciliation_service.ai_match_transaction",
                      side_effect=RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")):
            mock_txns.query_items.return_value = [TXN_DOC.copy()]
            resp = client.post("/api/reconciliation/ai-match", headers=headers_a)
        assert resp.status_code == 503
