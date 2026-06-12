import io
from unittest.mock import patch

from tests.conftest import TENANT_A, USER_A, auth_headers


class TestActivityEnrichment:

    def test_enrich_legacy_entry_backfills_display_fields(self):
        from smart_invoice_pro.utils.activity_enrichment import enrich_audit_entry

        entry = {
            "id": "log-legacy",
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "action": "CREATE",
            "entity": "invoice",
            "entity_id": "inv-1",
            "after": {"invoice_number": "INV-LEGACY", "status": "Draft"},
            "created_at": "2026-01-01T10:00:00",
        }
        enriched = enrich_audit_entry(entry)
        assert enriched["entity_label"] == "INV-LEGACY"
        assert enriched["summary"] == "INV-LEGACY created"
        assert enriched["category"] == "financial"
        assert enriched["risk_level"] == "medium"

    @patch("smart_invoice_pro.utils.cosmos_client.users_container")
    def test_enrich_lookup_user_profile(self, mock_users):
        from smart_invoice_pro.utils.activity_enrichment import clear_user_cache, enrich_audit_entry

        clear_user_cache()
        mock_users.query_items.return_value = [
            {"email": "user@example.com", "name": "Test User", "username": "testuser"}
        ]
        entry = {
            "action": "UPDATE",
            "entity": "customer",
            "entity_id": "cust-1",
            "user_id": USER_A,
            "before": {"name": "Old"},
            "after": {"name": "New"},
        }
        enriched = enrich_audit_entry(entry)
        assert enriched["user_name"] == "Test User"
        assert enriched["user_email"] == "user@example.com"


class TestAuditHelper:

    @patch("smart_invoice_pro.utils.audit_logger._fire_and_forget_write")
    def test_log_audit_event_shapes_document(self, mock_async_write):
        from smart_invoice_pro.utils.audit_logger import log_audit_event

        log_audit_event(
            {
                "tenant_id": TENANT_A,
                "user_id": USER_A,
                "action": "update",
                "entity": "invoice",
                "entity_id": "inv-1",
                "entity_label": "INV-001",
                "before": {"status": "draft", "password": "secret"},
                "after": {"status": "issued", "token": "abc"},
                "metadata": {"portal_password": "hidden", "note": "ok"},
            }
        )

        assert mock_async_write.called
        doc = mock_async_write.call_args[0][0]
        assert doc["tenant_id"] == TENANT_A
        assert doc["user_id"] == USER_A
        assert doc["action"] == "UPDATE"
        assert doc["entity"] == "invoice"
        assert doc["entity_id"] == "inv-1"
        assert doc["entity_label"] == "INV-001"
        assert doc["category"] == "financial"
        assert doc["risk_level"] == "medium"
        assert doc["summary"] == "INV-001 updated"
        assert doc["before"]["status"] == "draft"
        assert doc["after"]["status"] == "issued"
        assert "password" not in doc["before"]
        assert "token" not in doc["after"]
        assert "portal_password" not in doc["metadata"]

    @patch("smart_invoice_pro.utils.audit_logger._fire_and_forget_write")
    def test_log_audit_event_login_failed_risk(self, mock_async_write):
        from smart_invoice_pro.utils.audit_logger import log_audit_event

        log_audit_event({
            "tenant_id": TENANT_A,
            "action": "LOGIN_FAILED",
            "entity": "auth",
            "metadata": {"username": "demo"},
        })

        doc = mock_async_write.call_args[0][0]
        assert doc["action"] == "LOGIN_FAILED"
        assert doc["category"] == "security"
        assert doc["risk_level"] == "medium"


class TestAuditEndpoints:

    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    def test_get_activity_endpoint_returns_enriched_data(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [
            [1],
            [
                {
                    "id": "log-act",
                    "tenant_id": TENANT_A,
                    "action": "CREATE",
                    "entity": "bill",
                    "entity_id": "bill-1",
                    "after": {"bill_number": "BILL-99"},
                    "created_at": "2026-01-03T10:00:00",
                }
            ],
        ]

        resp = client.get("/api/activity?category=financial", headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert body["logs"][0]["entity_label"] == "BILL-99"
        assert body["logs"][0]["category"] == "financial"

    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    def test_get_audit_logs_returns_tenant_scoped_data(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [
            [1],
            [
                {
                    "id": "log-1",
                    "tenant_id": TENANT_A,
                    "action": "CREATE",
                    "entity": "invoice",
                    "entity_id": "inv-1",
                    "entity_label": "INV-001",
                    "summary": "INV-001 created",
                    "user_name": "Test User",
                    "category": "financial",
                    "risk_level": "medium",
                    "before": None,
                    "after": {"status": "draft"},
                    "created_at": "2026-01-01T10:00:00",
                }
            ],
        ]

        resp = client.get("/api/audit-logs?action=CREATE", headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert len(body["logs"]) == 1
        log = body["logs"][0]
        assert log["entity"] == "invoice"
        assert log["entity_label"] == "INV-001"
        assert log["summary"] == "INV-001 created"

    @patch("smart_invoice_pro.api.admin_api.audit_logs_container")
    def test_admin_audit_logs_requires_super_admin(self, mock_ctr, client, headers_a):
        resp = client.get("/api/admin/audit-logs", headers=headers_a)
        assert resp.status_code == 403

    @patch("smart_invoice_pro.api.admin_api.audit_logs_container")
    def test_admin_audit_logs_success(self, mock_ctr, client):
        super_admin_headers = auth_headers(user_id="super-admin", tenant_id="root-tenant", is_super_admin=True)
        mock_ctr.query_items.side_effect = [
            [1],
            [
                {
                    "id": "log-2",
                    "tenant_id": "tenant-x",
                    "action": "DELETE",
                    "entity_type": "customer",
                    "entity_id": "cust-1",
                    "changes": {"before": {"name": "Old"}, "after": None},
                    "timestamp": "2026-01-02T10:00:00",
                }
            ],
        ]

        resp = client.get("/api/admin/audit-logs?action=DELETE", headers=super_admin_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 1
        assert body["logs"][0]["entity"] == "customer"


class TestEntityActivityFeed:

    @patch("smart_invoice_pro.api.audit_logs_api.domain_events_container")
    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    def test_entity_activity_merges_audit_and_domain_events(self, mock_audit, mock_domain, client, headers_a):
        mock_audit.query_items.return_value = [
            {
                "id": "audit-1",
                "tenant_id": TENANT_A,
                "action": "CREATE",
                "entity": "invoice",
                "entity_id": "inv-1",
                "after": {"invoice_number": "INV-100"},
                "created_at": "2026-01-02T10:00:00",
            }
        ]
        mock_domain.query_items.return_value = [
            {
                "id": "dom-1",
                "tenant_id": TENANT_A,
                "event_type": "ENTITY_ARCHIVED",
                "entity_type": "invoice",
                "entity_id": "inv-1",
                "created_at": "2026-01-03T10:00:00",
            }
        ]

        resp = client.get("/api/activity/entity?entity_type=invoice&entity_id=inv-1", headers=headers_a)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total"] == 2
        assert body["logs"][0]["action"] == "ENTITY_ARCHIVED"
        assert body["logs"][1]["entity_label"] == "INV-100"

    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    def test_entity_activity_requires_params(self, mock_audit, client, headers_a):
        resp = client.get("/api/activity/entity", headers=headers_a)
        assert resp.status_code == 400


class TestWorkflowAuditEvents:

    @patch("smart_invoice_pro.api.invoices.log_audit_event")
    @patch("smart_invoice_pro.api.invoices.invoices_container")
    def test_invoice_payment_emits_payment_recorded(self, mock_ctr, mock_log, client, headers_a):
        mock_ctr.query_items.return_value = [{
            "id": "inv-pay",
            "tenant_id": TENANT_A,
            "invoice_number": "INV-PAY-1",
            "total_amount": 1000.0,
            "amount_paid": 0.0,
            "balance_due": 1000.0,
            "status": "Issued",
            "payment_history": [],
        }]

        resp = client.post(
            "/api/invoices/inv-pay/record-payment",
            json={"amount": 500, "payment_mode": "Cash", "payment_date": "2026-01-01"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "PAYMENT_RECORDED"
        assert payload["entity"] == "invoice"

    @patch("smart_invoice_pro.api.roles_api._get_role", return_value="Admin")
    @patch("smart_invoice_pro.api.roles_api.log_audit_event")
    @patch("smart_invoice_pro.api.roles_api.invoices_container")
    def test_invoice_approval_emits_workflow_event(self, mock_ctr, mock_log, _mock_role, client, headers_a):
        mock_ctr.query_items.return_value = [{
            "id": "inv-appr",
            "tenant_id": TENANT_A,
            "invoice_number": "INV-APP-1",
            "status": "Pending Approval",
        }]

        resp = client.post("/api/invoices/inv-appr/approve", headers=headers_a)
        assert resp.status_code == 200
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "APPROVAL_COMPLETED"
        assert payload["category"] == "workflow"


class TestBankingAuditEvents:

    @patch("smart_invoice_pro.api.bank_accounts_api.log_audit_event")
    @patch("smart_invoice_pro.api.bank_accounts_api.bank_accounts_container")
    def test_bank_account_create_emits_audit(self, mock_ctr, mock_log, client, headers_a):
        mock_ctr.create_item.return_value = {}

        resp = client.post(
            "/api/bank-accounts",
            json={"bank_name": "HDFC", "account_name": "Operating", "account_type": "current"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "BANK_ACCOUNT_CREATED"
        assert payload["category"] == "banking"
        assert payload["entity"] == "bank_account"

    @patch("smart_invoice_pro.api.bank_reconciliation_api._audit_banking")
    @patch("smart_invoice_pro.api.bank_reconciliation_api.bank_txns_container")
    def test_match_transaction_emits_reconciliation_matched(self, mock_txns, mock_audit, client, headers_a):
        mock_txns.query_items.return_value = [{
            "id": "txn-1",
            "user_id": USER_A,
            "description": "Vendor payment",
            "match_status": "unmatched",
        }]
        mock_txns.replace_item.return_value = {}

        resp = client.post(
            "/api/reconciliation/txn-1/match",
            json={"match_type": "invoice", "match_id": "inv-1"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        assert mock_audit.called
        args, kwargs = mock_audit.call_args
        assert args[0] == "RECONCILIATION_MATCHED"
        assert args[1] == "bank_transaction"
        assert kwargs["metadata"]["match_id"] == "inv-1"

    @patch("smart_invoice_pro.api.bank_reconciliation_api.record_domain_event")
    @patch("smart_invoice_pro.api.bank_reconciliation_api._audit_banking")
    @patch("smart_invoice_pro.api.bank_reconciliation_api.create_notification")
    @patch("smart_invoice_pro.api.bank_reconciliation_api.create_import_batch")
    def test_import_batch_created_emits_banking_audit(
        self, mock_create, mock_notify, mock_audit, mock_domain, client, headers_a
    ):
        mock_create.return_value = (
            {"id": "batch-1", "filename": "stmt.csv", "row_count": 2, "status": "review_ready", "workflow_mode": "deterministic_parse"},
            {"id": "job-1", "status": "completed"},
            [],
        )

        resp = client.post(
            "/api/reconciliation/import-batches",
            data={"file": (io.BytesIO(b"date,desc\n2024-01-01,test"), "stmt.csv"), "bank_account_id": "ba-1"},
            content_type="multipart/form-data",
            headers={"Authorization": headers_a["Authorization"]},
        )
        assert resp.status_code == 201
        assert mock_audit.called
        assert mock_audit.call_args[0][0] == "BANK_IMPORT_BATCH_CREATED"
        assert mock_domain.called

    @patch("smart_invoice_pro.services.bank_import.import_workflow_service.record_domain_event")
    @patch("smart_invoice_pro.services.bank_import.import_workflow_service.log_audit_event")
    @patch("smart_invoice_pro.services.bank_import.import_workflow_service.bank_import_rows_container")
    @patch("smart_invoice_pro.services.bank_import.import_workflow_service._replace_job")
    @patch("smart_invoice_pro.services.bank_import.import_workflow_service._replace_batch")
    def test_import_job_success_emits_completed_audit(
        self, mock_replace_batch, mock_replace_job, mock_rows, mock_log, mock_domain
    ):
        from smart_invoice_pro.services.bank_import.import_workflow_service import _run_import_job

        batch_doc = {
            "id": "batch-ok",
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "bank_account_id": "ba-1",
            "filename": "stmt.csv",
            "warnings": [],
        }
        job_doc = {"id": "job-ok", "status": "queued"}
        file_profile = {"extension": "csv", "workflow_mode": "deterministic_parse"}
        csv_bytes = b"date,description,debit,credit,balance\n2024-01-15,Acme,,1000.00,5000.00\n"

        _run_import_job(
            batch_doc=batch_doc,
            job_doc=job_doc,
            file_profile=file_profile,
            file_bytes=csv_bytes,
        )

        assert mock_log.called
        assert mock_log.call_args[0][0]["action"] == "BANK_IMPORT_COMPLETED"
        assert mock_domain.called
        assert mock_domain.call_args[0][0] == "BANK_IMPORT_COMPLETED"

    @patch("smart_invoice_pro.utils.audit_logger._fire_and_forget_write")
    def test_banking_audit_summary_and_category(self, mock_write):
        from smart_invoice_pro.utils.audit_logger import log_audit_event

        log_audit_event({
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "action": "RECONCILIATION_MATCHED",
            "entity": "bank_transaction",
            "entity_id": "txn-1",
            "entity_label": "Vendor payment",
            "category": "banking",
        })
        doc = mock_write.call_args[0][0]
        assert doc["category"] == "banking"
        assert "reconciled" in doc["summary"].lower()


class TestAuditExport:

    @patch("smart_invoice_pro.api.audit_logs_api.audit_logs_container")
    def test_activity_export_returns_csv(self, mock_ctr, client, headers_a):
        mock_ctr.query_items.side_effect = [
            [1],
            [{
                "id": "log-export",
                "tenant_id": TENANT_A,
                "action": "CREATE",
                "entity": "invoice",
                "entity_id": "inv-1",
                "entity_label": "INV-EXPORT",
                "summary": "INV-EXPORT created",
                "user_name": "Test User",
                "category": "financial",
                "risk_level": "medium",
                "created_at": "2026-01-01T10:00:00",
            }],
        ]

        resp = client.get("/api/activity/export?category=financial", headers=headers_a)
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type
        body = resp.get_data(as_text=True)
        assert "INV-EXPORT" in body
        assert "created_at" in body.splitlines()[0]

    def test_audit_export_serializes_rows(self):
        from smart_invoice_pro.utils.audit_export import audit_rows_to_csv

        csv_text = audit_rows_to_csv([
            {
                "created_at": "2026-01-01",
                "action": "UPDATE",
                "summary": "GST updated",
                "entity": "organization_profile",
            }
        ])
        assert "GST updated" in csv_text
        assert "organization_profile" in csv_text


class TestAuditRetention:

    def test_retention_disabled_by_default(self):
        from smart_invoice_pro.utils.audit_retention import archive_expired_audit_logs

        result = archive_expired_audit_logs()
        assert result["enabled"] is False
        assert result["archived"] == 0

    @patch("smart_invoice_pro.utils.audit_retention.audit_logs_container")
    @patch("smart_invoice_pro.utils.audit_retention.audit_logs_archive_container")
    def test_retention_archives_old_rows(self, mock_archive, mock_live):
        from smart_invoice_pro.utils.audit_retention import archive_expired_audit_logs

        mock_live.query_items.return_value = [{
            "id": "old-log",
            "tenant_id": TENANT_A,
            "created_at": "2020-01-01T00:00:00",
        }]

        result = archive_expired_audit_logs(retention_days_override=30)
        assert result["enabled"] is True
        assert result["archived"] == 1
        assert mock_archive.create_item.called
        assert mock_live.delete_item.called


class TestAuditWriteMonitoring:

    @patch("smart_invoice_pro.utils.audit_logger.audit_logs_container")
    def test_write_stats_track_success_and_failure(self, mock_ctr):
        from smart_invoice_pro.utils.audit_logger import _WRITE_STATS, _write_audit_doc

        before = dict(_WRITE_STATS)
        mock_ctr.create_item.side_effect = [None, RuntimeError("cosmos down")]
        _write_audit_doc({"id": "ok", "tenant_id": TENANT_A})
        _write_audit_doc({"id": "fail", "tenant_id": TENANT_A})

        assert _WRITE_STATS["attempted"] == before["attempted"] + 2
        assert _WRITE_STATS["succeeded"] == before["succeeded"] + 1
        assert _WRITE_STATS["failed"] == before["failed"] + 1

    @patch("smart_invoice_pro.api.admin_api.get_audit_write_stats", return_value={"attempted": 5, "succeeded": 5, "failed": 0})
    def test_admin_audit_stats_endpoint(self, _mock_stats, client):
        super_admin_headers = auth_headers(user_id="super-admin", tenant_id="root-tenant", is_super_admin=True)
        resp = client.get("/api/admin/audit-stats", headers=super_admin_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["write_stats"]["attempted"] == 5
        assert "retention_days" in body


class TestAuditIntegration:

    @patch("smart_invoice_pro.api.product_api.log_audit_event")
    @patch("smart_invoice_pro.api.product_api.products_container")
    def test_product_create_emits_audit_event(self, mock_products, mock_log, client, headers_a):
        mock_products.query_items.return_value = []

        resp = client.post(
            "/api/products",
            json={"name": "Widget", "price": 100, "unit": "Nos"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "CREATE"
        assert payload["entity"] == "product"

    @patch("smart_invoice_pro.api.routes.log_audit_event")
    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_emits_audit_event(self, mock_users, mock_refresh, mock_log, client):
        from werkzeug.security import generate_password_hash

        mock_users.query_items.return_value = [
            {
                "id": "user-1",
                "tenant_id": TENANT_A,
                "username": "demo",
                "password": generate_password_hash("secret123", method="pbkdf2:sha256", salt_length=16),
            }
        ]

        resp = client.post("/api/auth/login", json={"username": "demo", "password": "secret123"})
        assert resp.status_code == 200
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "LOGIN"
        assert payload["entity"] == "auth"

    @patch("smart_invoice_pro.api.routes.log_audit_event")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_failed_login_emits_audit_event(self, mock_users, mock_log, client):
        mock_users.query_items.return_value = []

        resp = client.post("/api/auth/login", json={"username": "unknown", "password": "bad"})
        assert resp.status_code == 401
        assert mock_log.called
        payload = mock_log.call_args[0][0]
        assert payload["action"] == "LOGIN_FAILED"
        assert payload["entity"] == "auth"

    @patch("smart_invoice_pro.api.bills_api.log_audit")
    @patch("smart_invoice_pro.api.bills_api.bills_container")
    @patch("smart_invoice_pro.utils.stock_utils.product_exists_for_tenant", return_value=True)
    def test_bill_create_emits_audit(self, _mock_stock, mock_bills, mock_log, client, headers_a):
        created = {
            "id": "bill-1",
            "bill_number": "BILL-001",
            "tenant_id": TENANT_A,
            "total_amount": 100.0,
            "payment_status": "Unpaid",
        }
        mock_bills.create_item.return_value = created

        resp = client.post(
            "/api/bills",
            json={
                "bill_number": "BILL-001",
                "vendor_id": "v-1",
                "bill_date": "2026-01-01",
                "due_date": "2026-01-15",
                "total_amount": 100.0,
            },
            headers=headers_a,
        )
        assert resp.status_code == 201
        assert mock_log.called
        assert mock_log.call_args[0][0] == "bill"
        assert mock_log.call_args[0][1] == "create"
