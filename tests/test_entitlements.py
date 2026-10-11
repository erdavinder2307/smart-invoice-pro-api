"""
Tests for plan limits (utils/entitlements.py) and the seat check on
POST /api/settings/users and the reactivation branch of PUT /api/settings/users/<id>.
"""
from unittest.mock import patch

import pytest

from smart_invoice_pro.utils import entitlements
from tests.conftest import TENANT_A, USER_A, auth_headers
from tests.test_roles_permissions import ADMIN_USER, SALES_ROLE_DOC, _mock_roles_ctr, _patches


def _tenant(plan="trial", tenant_type="PRODUCTION"):
    return {"id": TENANT_A, "name": "Acme", "plan": plan, "status": "active", "tenant_type": tenant_type}


def _users(n, role="Sales"):
    return [
        {"id": f"u{i}", "username": f"u{i}", "role": role, "tenant_id": TENANT_A, "is_active": True}
        for i in range(n)
    ]


INVITE = {
    "name": "New User",
    "email": "new@example.com",
    "username": "newuser",
    "password": "securepass123",
    "role": "Sales",
}


def _invite(client, headers, tenant, active_users, role="Sales"):
    p1, p2, p3 = _patches()
    mock_rctr = _mock_roles_ctr()
    with p1 as mock_users, p2 as mock_roles_fn, p3 as mock_role_users, \
            patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=tenant), \
            patch("smart_invoice_pro.api.roles_permissions_api._active_account_users",
                  return_value=active_users):
        mock_role_users.query_items.return_value = [ADMIN_USER]
        mock_roles_fn.return_value = mock_rctr
        mock_rctr.query_items.return_value = [dict(SALES_ROLE_DOC, name=role)]
        mock_users.query_items.return_value = []
        resp = client.post("/api/settings/users", json=dict(INVITE, role=role), headers=headers)
        return resp, mock_users


class TestResolvePlan:
    @pytest.mark.parametrize("stored,expected", [
        ("trial", "trial"), ("starter", "starter"), ("growth", "growth"),
        ("pro", "growth"), ("Enterprise", "growth"), (None, "trial"), ("unknown", "trial"),
    ])
    def test_aliases_and_fallback(self, stored, expected):
        assert entitlements.resolve_plan(stored) == expected


class TestInviteSeatLimit:
    def test_under_limit_creates_user(self, client, headers_a):
        resp, mock_users = _invite(client, headers_a, _tenant("starter"), _users(2))
        assert resp.status_code == 201
        mock_users.create_item.assert_called_once()

    def test_at_limit_returns_402(self, client, headers_a):
        resp, mock_users = _invite(client, headers_a, _tenant("starter"), _users(3))
        assert resp.status_code == 402
        body = resp.get_json()
        assert body["error"] == "plan_limit"
        assert body["code"] == "seat_limit"
        assert body["upgrade_url"] == "/settings/billing"
        assert "3 users" in body["message"]
        mock_users.create_item.assert_not_called()

    def test_over_limit_returns_402(self, client, headers_a):
        resp, mock_users = _invite(client, headers_a, _tenant("starter"), _users(5))
        assert resp.status_code == 402
        mock_users.create_item.assert_not_called()

    def test_trial_tenant_limited_to_three(self, client, headers_a):
        resp, _ = _invite(client, headers_a, _tenant("trial"), _users(2))
        assert resp.status_code == 201
        resp, _ = _invite(client, headers_a, _tenant("trial"), _users(3))
        assert resp.status_code == 402

    def test_legacy_pro_plan_gets_growth_seats(self, client, headers_a):
        resp, _ = _invite(client, headers_a, _tenant("pro"), _users(9))
        assert resp.status_code == 201
        resp, _ = _invite(client, headers_a, _tenant("pro"), _users(10))
        assert resp.status_code == 402

    def test_first_accountant_is_free(self, client, headers_a):
        resp, _ = _invite(client, headers_a, _tenant("starter"), _users(3), role="Accountant")
        assert resp.status_code == 201

    def test_second_accountant_takes_a_seat(self, client, headers_a):
        active = _users(2) + _users(1, role="Accountant")
        resp, _ = _invite(client, headers_a, _tenant("starter"), active, role="Accountant")
        assert resp.status_code == 201  # 2 seats used + this one = 3
        active = _users(3) + _users(1, role="Accountant")
        resp, _ = _invite(client, headers_a, _tenant("starter"), active, role="Accountant")
        assert resp.status_code == 402

    def test_free_accountant_not_counted_for_other_roles(self, client, headers_a):
        active = _users(2) + _users(1, role="Accountant")
        resp, _ = _invite(client, headers_a, _tenant("starter"), active)
        assert resp.status_code == 201

    def test_internal_tenant_not_limited(self, client, headers_a):
        resp, _ = _invite(client, headers_a, _tenant("starter", "INTERNAL"), _users(20))
        assert resp.status_code == 201

    def test_demo_tenant_type_not_limited(self):
        with patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id",
                   return_value=_tenant("starter", "DEMO")):
            from smart_invoice_pro.app import create_app
            with create_app().test_request_context():
                assert entitlements.check_seat_available(TENANT_A, _users(20), "Sales") is None

    def test_super_admin_not_limited(self, client):
        headers = auth_headers(user_id=USER_A, tenant_id=TENANT_A, is_super_admin=True)
        resp, _ = _invite(client, headers, _tenant("starter"), _users(3))
        assert resp.status_code == 201

    def test_missing_tenant_document_not_limited(self, client, headers_a):
        resp, _ = _invite(client, headers_a, None, _users(30))
        assert resp.status_code == 201


class TestReactivationSeatLimit:
    def _put(self, client, headers, tenant, active_users, target, body):
        p1, p2, p3 = _patches()
        with p1 as mock_users, p2, p3 as mock_role_users, \
                patch("smart_invoice_pro.api.roles_permissions_api._fetch_user_by_id",
                      return_value=dict(target)), \
                patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=tenant), \
                patch("smart_invoice_pro.api.roles_permissions_api._active_account_users",
                      return_value=active_users):
            mock_role_users.query_items.return_value = [ADMIN_USER]
            resp = client.put(f"/api/settings/users/{target['id']}", json=body, headers=headers)
            return resp, mock_users

    INACTIVE = {"id": "u-old", "username": "old", "role": "Sales", "tenant_id": TENANT_A, "is_active": False}

    def test_reactivate_at_limit_returns_402(self, client, headers_a):
        resp, mock_users = self._put(client, headers_a, _tenant("starter"), _users(3),
                                     self.INACTIVE, {"is_active": True})
        assert resp.status_code == 402
        assert resp.get_json()["code"] == "seat_limit"
        mock_users.upsert_item.assert_not_called()

    def test_reactivate_under_limit_ok(self, client, headers_a):
        resp, mock_users = self._put(client, headers_a, _tenant("starter"), _users(2),
                                     self.INACTIVE, {"is_active": True})
        assert resp.status_code == 200
        mock_users.upsert_item.assert_called_once()

    def test_update_of_active_user_skips_seat_check(self, client, headers_a):
        active = dict(self.INACTIVE, is_active=True)
        resp, _ = self._put(client, headers_a, _tenant("starter"), _users(5),
                            active, {"is_active": True, "name": "Renamed"})
        assert resp.status_code == 200

    def test_deactivate_never_blocked(self, client, headers_a):
        active = dict(self.INACTIVE, is_active=True)
        resp, _ = self._put(client, headers_a, _tenant("starter"), _users(5),
                            active, {"is_active": False})
        assert resp.status_code == 200


class TestRequireEntitlement:
    def _call(self, tenant, feature="approvals", **ctx):
        from smart_invoice_pro.app import create_app

        @entitlements.require_entitlement(feature)
        def view():
            return "ok"

        with patch("smart_invoice_pro.utils.entitlements.get_tenant_by_id", return_value=tenant):
            with create_app().test_request_context():
                from flask import g, request
                request.tenant_id = TENANT_A
                for k, v in ctx.items():
                    setattr(g, k, v)
                return view()

    def test_feature_in_plan_passes(self):
        assert self._call(_tenant("growth")) == "ok"

    def test_feature_missing_returns_402(self):
        resp, status = self._call(_tenant("starter"))
        assert status == 402
        assert resp.get_json()["code"] == "feature"

    def test_super_admin_passes(self):
        assert self._call(_tenant("starter"), is_super_admin=True) == "ok"


class TestActiveAccountUsers:
    def test_filters_inactive_side_docs_and_excluded(self):
        from smart_invoice_pro.api import roles_permissions_api as rp
        docs = [
            {"id": "a", "username": "a", "is_active": True},
            {"id": "b", "username": "b"},                       # no flag = active
            {"id": "c", "username": "c", "is_active": False},
            {"id": "d", "type": "user_profile", "username": "d"},
            {"id": "e", "username": "e"},
        ]
        with patch.object(rp, "users_container") as mock_users:
            mock_users.query_items.return_value = docs
            ids = [u["id"] for u in rp._active_account_users(TENANT_A, exclude_user_id="e")]
        assert ids == ["a", "b"]
