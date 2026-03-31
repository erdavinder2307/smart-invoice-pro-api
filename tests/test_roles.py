"""
Tests for Roles & Approvals API (roles_api.py).
GET /api/my-role, GET /api/users, PUT /api/users/<id>/role,
GET /api/approvals/pending,
Invoice approval workflow (submit, approve, reject),
Purchase Order approval workflow (submit, approve, reject).
"""
import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import TENANT_A, TENANT_B, USER_A, USER_B


ADMIN_USER = {
    "id": USER_A,
    "username": "admin_user",
    "email": "admin@example.com",
    "role": "Admin",
    "created_at": "2024-01-01T00:00:00",
}

SALES_USER = {
    "id": USER_B,
    "username": "sales_user",
    "email": "sales@example.com",
    "role": "Sales",
    "created_at": "2024-01-01T00:00:00",
}

DRAFT_INVOICE = {
    "id": "inv-001",
    "invoice_number": "INV-001",
    "customer_name": "Acme",
    "total_amount": 1000,
    "status": "Draft",
    "tenant_id": TENANT_A,
}

PENDING_INVOICE = {
    "id": "inv-002",
    "invoice_number": "INV-002",
    "customer_name": "Beta Corp",
    "total_amount": 2000,
    "status": "Pending Approval",
    "submitted_by": USER_A,
    "tenant_id": TENANT_A,
}

DRAFT_PO = {
    "id": "po-001",
    "po_number": "PO-001",
    "vendor_name": "Vendor X",
    "total_amount": 500,
    "status": "Draft",
    "tenant_id": TENANT_A,
}

PENDING_PO = {
    "id": "po-002",
    "po_number": "PO-002",
    "vendor_name": "Vendor Y",
    "total_amount": 3000,
    "status": "Pending Approval",
    "submitted_by": USER_A,
    "tenant_id": TENANT_A,
}


def _patch_roles_containers():
    """Patch all containers used by roles_api."""
    return (
        patch("smart_invoice_pro.api.roles_api.users_container"),
        patch("smart_invoice_pro.api.roles_api.invoices_container"),
        patch("smart_invoice_pro.api.roles_api.purchase_orders_container"),
    )


class TestGetMyRole:
    """GET /api/my-role"""

    def test_returns_role(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.get("/api/my-role", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role"] == "Admin"
        assert data["user_id"] == USER_A

    def test_user_not_found(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.return_value = []
            resp = client.get("/api/my-role", headers=headers_a)
        assert resp.status_code == 404


class TestListUsers:
    """GET /api/users (Admin only)"""

    def test_admin_can_list(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            # require_role calls query_items to find user + read_all_items to list
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_users.read_all_items.return_value = [ADMIN_USER, SALES_USER]
            resp = client.get("/api/users", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_non_admin_forbidden(self, client, headers_b):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.return_value = [SALES_USER]
            resp = client.get("/api/users", headers=headers_b)
        assert resp.status_code == 403


class TestUpdateUserRole:
    """PUT /api/users/<id>/role (Admin only)"""

    def test_update_success(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            # 1st call: require_role → _get_role → _fetch_user (Admin check)
            # 2nd call: update_user_role → _fetch_user for target
            mock_users.query_items.side_effect = [
                [ADMIN_USER],  # require_role
                [SALES_USER],  # _fetch_user for target
            ]
            resp = client.put(f"/api/users/{USER_B}/role",
                              json={"role": "Manager"}, headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role"] == "Manager"

    def test_invalid_role(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            resp = client.put(f"/api/users/{USER_B}/role",
                              json={"role": "SuperAdmin"}, headers=headers_a)
        assert resp.status_code == 400
        assert "invalid role" in resp.get_json()["error"].lower()

    def test_prevent_last_admin_removal(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.side_effect = [
                [ADMIN_USER],  # require_role → _fetch_user
                [ADMIN_USER],  # _fetch_user for target (same admin user)
                [ADMIN_USER],  # admin count check → only 1 admin
            ]
            resp = client.put(f"/api/users/{USER_A}/role",
                              json={"role": "Sales"}, headers=headers_a)
        assert resp.status_code == 400
        assert "last admin" in resp.get_json()["error"].lower()

    def test_target_not_found(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3:
            mock_users.query_items.side_effect = [
                [ADMIN_USER],  # require_role → _fetch_user
                [],             # target not found
            ]
            resp = client.put("/api/users/nonexistent/role",
                              json={"role": "Manager"}, headers=headers_a)
        assert resp.status_code == 404


class TestPendingApprovals:
    """GET /api/approvals/pending"""

    def test_returns_pending_items(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [PENDING_INVOICE]
            mock_po.query_items.return_value = [PENDING_PO]
            resp = client.get("/api/approvals/pending", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        assert len(data["invoices"]) == 1
        assert len(data["purchase_orders"]) == 1

    def test_empty_pending(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = []
            mock_po.query_items.return_value = []
            resp = client.get("/api/approvals/pending", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 0


class TestInvoiceApprovalWorkflow:
    """Submit, approve, reject invoice."""

    def test_submit_draft_invoice(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [DRAFT_INVOICE.copy()]
            resp = client.post("/api/invoices/inv-001/submit-for-approval",
                               headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Pending Approval"

    def test_submit_non_draft_fails(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [PENDING_INVOICE.copy()]
            resp = client.post("/api/invoices/inv-002/submit-for-approval",
                               headers=headers_a)
        assert resp.status_code == 400

    def test_submit_not_found(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = []
            resp = client.post("/api/invoices/bad-id/submit-for-approval",
                               headers=headers_a)
        assert resp.status_code == 404

    def test_approve_pending_invoice(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [PENDING_INVOICE.copy()]
            resp = client.post("/api/invoices/inv-002/approve",
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "Issued"
        assert data["approved_by"] == USER_A

    def test_approve_non_pending_fails(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [DRAFT_INVOICE.copy()]
            resp = client.post("/api/invoices/inv-001/approve",
                               headers=headers_a)
        assert resp.status_code == 400

    def test_reject_pending_invoice(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = [PENDING_INVOICE.copy()]
            resp = client.post("/api/invoices/inv-002/reject",
                               json={"reason": "Bad amounts"},
                               headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Draft"

    def test_reject_not_found(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2 as mock_inv, p3:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_inv.query_items.return_value = []
            resp = client.post("/api/invoices/bad-id/reject",
                               json={"reason": "test"}, headers=headers_a)
        assert resp.status_code == 404


class TestPOApprovalWorkflow:
    """Submit, approve, reject purchase order."""

    def test_submit_draft_po(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = [DRAFT_PO.copy()]
            resp = client.post("/api/purchase-orders/po-001/submit-for-approval",
                               headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Pending Approval"

    def test_submit_non_draft_po_fails(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = [PENDING_PO.copy()]
            resp = client.post("/api/purchase-orders/po-002/submit-for-approval",
                               headers=headers_a)
        assert resp.status_code == 400

    def test_approve_pending_po(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = [PENDING_PO.copy()]
            resp = client.post("/api/purchase-orders/po-002/approve",
                               headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "Sent"

    def test_approve_po_not_found(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = []
            resp = client.post("/api/purchase-orders/bad-id/approve",
                               headers=headers_a)
        assert resp.status_code == 404

    def test_reject_pending_po(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = [PENDING_PO.copy()]
            resp = client.post("/api/purchase-orders/po-002/reject",
                               json={"reason": "Over budget"},
                               headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Draft"

    def test_reject_po_non_pending_fails(self, client, headers_a):
        p1, p2, p3 = _patch_roles_containers()
        with p1 as mock_users, p2, p3 as mock_po:
            mock_users.query_items.return_value = [ADMIN_USER]
            mock_po.query_items.return_value = [DRAFT_PO.copy()]
            resp = client.post("/api/purchase-orders/po-001/reject",
                               json={"reason": "test"}, headers=headers_a)
        assert resp.status_code == 400
