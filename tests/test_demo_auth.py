"""Tests for Interactive Workspace (demo) auth and guards."""

import datetime
import os
from unittest.mock import patch

import jwt
import pytest

from tests.conftest import USER_A, make_token

DEMO_TENANT = "d3m00000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _demo_env():
    with patch.dict(
        os.environ,
        {
            "DEMO_ENABLED": "true",
            "DEMO_TENANT_ID": DEMO_TENANT,
        },
        clear=False,
    ):
        yield


class TestDemoRoles:
    def test_demo_roles_when_enabled(self, client):
        resp = client.get("/api/auth/demo-roles")
        assert resp.status_code == 200
        roles = resp.get_json()["roles"]
        assert len(roles) == 4
        titles = {r["title"] for r in roles}
        assert "Business Owner" in titles
        assert "Finance Manager" in titles


class TestDemoLogin:
    @patch("smart_invoice_pro.api.routes.log_audit_event")
    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_demo_login_success(self, mock_users, mock_tokens, mock_audit, client):
        mock_users.query_items.return_value = [{
            "id": USER_A,
            "userid": USER_A,
            "tenant_id": DEMO_TENANT,
            "username": "demo-manager",
            "role": "Manager",
            "is_demo_user": True,
            "is_active": True,
        }]
        mock_tokens.create_item.return_value = None

        resp = client.post(
            "/api/auth/demo-login",
            json={"role": "Manager"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("access_token") or body.get("token")
        assert body.get("user", {}).get("is_demo") is True


class TestRefreshTokenDemo:
    def test_refresh_preserves_is_demo(self, client):
        jwt_secret = os.getenv("JWT_SECRET_KEY", "your_secret_key")

        with patch("smart_invoice_pro.api.routes.refresh_tokens_container") as mock_rt, \
             patch("smart_invoice_pro.api.routes.users_container") as mock_users:
            mock_rt.query_items.return_value = [{
                "id": "sess-1",
                "user_id": USER_A,
                "tenant_id": DEMO_TENANT,
                "token": "refresh-abc",
                "expires_at": (datetime.datetime.utcnow() + datetime.timedelta(hours=2)).isoformat(),
                "is_demo": True,
            }]
            mock_users.query_items.return_value = [{
                "id": USER_A,
                "username": "demo-manager",
                "role": "Manager",
                "is_demo_user": True,
            }]
            mock_rt.delete_item.return_value = None
            mock_rt.create_item.return_value = None

            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "refresh-abc"},
            )
        assert resp.status_code == 200
        new_token = resp.get_json()["access_token"]
        payload = jwt.decode(new_token, jwt_secret, algorithms=["HS256"])
        assert payload.get("is_demo") is True


class TestDemoCreateLimit:
    @patch("smart_invoice_pro.utils.demo_guard._count_tenant_records", return_value=20)
    def test_customer_create_blocked_at_limit(self, mock_count, client):
        token = make_token(user_id=USER_A, tenant_id=DEMO_TENANT, is_demo=True)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        with patch(
            "smart_invoice_pro.utils.permission_checker._get_user_permissions",
            return_value=(True, {}),
        ):
            resp = client.post(
                "/api/customers",
                headers=headers,
                json={"display_name": "Extra Customer", "customer_type": "business"},
            )
        assert resp.status_code == 403
        assert resp.get_json().get("code") == "demo_create_limit"
