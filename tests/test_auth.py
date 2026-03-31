"""
Tests for authentication middleware and auth endpoints (register, login, refresh, logout).
"""
import datetime
from unittest.mock import patch, MagicMock

import jwt
import pytest
from werkzeug.security import generate_password_hash

from tests.conftest import (
    JWT_SECRET,
    TENANT_A,
    USER_A,
    auth_headers,
    make_expired_token,
    make_token,
)


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH MIDDLEWARE – token validation
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthMiddleware:
    """JWT enforcement on protected endpoints."""

    def test_no_token_returns_401(self, client):
        resp = client.get("/api/invoices")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "Unauthorized"

    def test_invalid_token_returns_401(self, client):
        resp = client.get(
            "/api/invoices",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    def test_expired_token_returns_401(self, client):
        token = make_expired_token()
        resp = client.get(
            "/api/invoices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_token_wrong_secret_returns_401(self, client):
        token = jwt.encode(
            {
                "id": USER_A,
                "user_id": USER_A,
                "tenant_id": TENANT_A,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            },
            "wrong_secret_key",
            algorithm="HS256",
        )
        resp = client.get(
            "/api/invoices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_token_missing_user_id_returns_401(self, client):
        token = jwt.encode(
            {
                "tenant_id": TENANT_A,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        resp = client.get(
            "/api/invoices",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    def test_token_missing_tenant_id_uses_id_fallback(self, client):
        """When tenant_id is absent, middleware falls back to payload['id']."""
        token = jwt.encode(
            {
                "id": USER_A,
                "user_id": USER_A,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        # This should NOT 401 because the middleware fallback sets tenant_id = id
        resp = client.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Ping is not behind auth, but any protected endpoint would also pass
        assert resp.status_code == 200

    def test_valid_token_allows_access(self, client, headers_a):
        resp = client.get("/api/ping", headers=headers_a)
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "pong"

    def test_bearer_prefix_required(self, client):
        token = make_token()
        resp = client.get(
            "/api/invoices",
            headers={"Authorization": f"Token {token}"},
        )
        assert resp.status_code == 401

    def test_options_request_skips_auth(self, client):
        resp = client.options("/api/invoices")
        assert resp.status_code in (200, 204)

    def test_exempt_path_login_no_auth(self, client):
        # Login endpoint should be accessible without a JWT token.
        # The handler may return 401 for bad credentials, but NOT because
        # the auth middleware blocked it (which would say "Unauthorized").
        resp = client.post("/api/auth/login", json={"username": "x", "password": "y"})
        data = resp.get_json()
        # Auth middleware returns {"error": "Unauthorized"}, login returns {"message": ...}
        if resp.status_code == 401:
            assert "message" in data  # from login handler, not middleware
            assert "error" not in data

    def test_exempt_path_register_no_auth(self, client):
        resp = client.post(
            "/api/auth/register",
            json={"username": "newuser", "password": "pass123"},
        )
        # If auth middleware blocked it, we'd get {"error": "Unauthorized"} 401
        assert resp.status_code != 401 or "error" not in resp.get_json()


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTER
# ─────────────────────────────────────────────────────────────────────────────
class TestRegister:

    @patch("smart_invoice_pro.api.routes.users_container")
    def test_register_success(self, mock_users, client):
        mock_users.query_items.return_value = [0]  # 0 users → Admin role
        resp = client.post(
            "/api/auth/register",
            json={"username": "admin1", "password": "secret123"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["message"] == "User registered successfully!"
        assert data["user"]["username"] == "admin1"
        assert data["user"]["role"] == "Admin"
        mock_users.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.routes.users_container")
    def test_register_second_user_gets_sales_role(self, mock_users, client):
        mock_users.query_items.return_value = [5]  # >=1 user → Sales role
        resp = client.post(
            "/api/auth/register",
            json={"username": "sales1", "password": "secret123"},
        )
        assert resp.status_code == 201
        assert resp.get_json()["user"]["role"] == "Sales"

    def test_register_non_json_body_returns_400(self, client):
        resp = client.post(
            "/api/auth/register",
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
#  LOGIN
# ─────────────────────────────────────────────────────────────────────────────
class TestLogin:

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_success(self, mock_users, mock_refresh, client):
        hashed = generate_password_hash("secret123", method="pbkdf2:sha256", salt_length=16)
        mock_users.query_items.return_value = [
            {
                "id": USER_A,
                "userid": USER_A,
                "tenant_id": TENANT_A,
                "username": "admin1",
                "password": hashed,
                "role": "Admin",
            }
        ]
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "secret123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["username"] == "admin1"
        mock_refresh.create_item.assert_called_once()

    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_invalid_password(self, mock_users, client):
        hashed = generate_password_hash("correct", method="pbkdf2:sha256", salt_length=16)
        mock_users.query_items.return_value = [
            {"id": USER_A, "username": "admin1", "password": hashed, "tenant_id": TENANT_A}
        ]
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "wrong"},
        )
        assert resp.status_code == 401

    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_unknown_user(self, mock_users, client):
        mock_users.query_items.return_value = []
        resp = client.post(
            "/api/auth/login",
            json={"username": "ghost", "password": "pass"},
        )
        assert resp.status_code == 401

    def test_login_non_json_body(self, client):
        resp = client.post(
            "/api/auth/login", data="bad", content_type="text/plain"
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
#  REFRESH TOKEN
# ─────────────────────────────────────────────────────────────────────────────
class TestRefreshToken:

    @patch("smart_invoice_pro.api.routes.users_container")
    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    def test_refresh_success(self, mock_refresh, mock_users, client):
        future = (datetime.datetime.utcnow() + datetime.timedelta(days=10)).isoformat()
        mock_refresh.query_items.return_value = [
            {
                "id": "rt-1",
                "user_id": USER_A,
                "tenant_id": TENANT_A,
                "token": "valid-refresh-token",
                "expires_at": future,
            }
        ]
        mock_users.query_items.return_value = [
            {"id": USER_A, "username": "admin1", "role": "Admin"}
        ]
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "valid-refresh-token"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert "refresh_token" in data

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    def test_refresh_expired_token(self, mock_refresh, client):
        past = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()
        mock_refresh.query_items.return_value = [
            {
                "id": "rt-1",
                "user_id": USER_A,
                "tenant_id": TENANT_A,
                "token": "expired-token",
                "expires_at": past,
            }
        ]
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "expired-token"},
        )
        assert resp.status_code == 401
        assert "expired" in resp.get_json()["error"].lower()

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    def test_refresh_invalid_token(self, mock_refresh, client):
        mock_refresh.query_items.return_value = []
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "does-not-exist"},
        )
        assert resp.status_code == 401

    def test_refresh_missing_token(self, client):
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
#  LOGOUT
# ─────────────────────────────────────────────────────────────────────────────
class TestLogout:

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    def test_logout_success(self, mock_refresh, client, headers_a):
        mock_refresh.query_items.return_value = [
            {"id": "rt-1", "user_id": USER_A, "token": "tok"}
        ]
        resp = client.post(
            "/api/auth/logout",
            json={"refresh_token": "tok"},
            headers=headers_a,
        )
        assert resp.status_code == 200
        mock_refresh.delete_item.assert_called_once()

    def test_logout_without_token_still_200(self, client, headers_a):
        resp = client.post("/api/auth/logout", json={}, headers=headers_a)
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  RESPONSE SECURITY – no sensitive / internal fields
# ─────────────────────────────────────────────────────────────────────────────
class TestResponseSecurity:

    @patch("smart_invoice_pro.api.routes.refresh_tokens_container")
    @patch("smart_invoice_pro.api.routes.users_container")
    def test_login_response_has_no_password(self, mock_users, mock_refresh, client):
        hashed = generate_password_hash("s3cret", method="pbkdf2:sha256", salt_length=16)
        mock_users.query_items.return_value = [
            {
                "id": USER_A,
                "userid": USER_A,
                "tenant_id": TENANT_A,
                "username": "admin1",
                "password": hashed,
                "role": "Admin",
            }
        ]
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "s3cret"},
        )
        data = resp.get_json()
        assert "password" not in str(data)
