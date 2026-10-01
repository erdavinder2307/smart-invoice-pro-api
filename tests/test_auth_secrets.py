"""The app must refuse to start, and never sign tokens, without its token secrets."""
import pytest

from smart_invoice_pro.api.auth_middleware import get_customer_jwt_secret, get_jwt_secret
from smart_invoice_pro.app import create_app


class TestStartupRequiresSecrets:
    def test_missing_jwt_secret_stops_startup(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            create_app()

    def test_missing_customer_secret_stops_startup(self, monkeypatch):
        monkeypatch.delenv("CUSTOMER_JWT_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CUSTOMER_JWT_SECRET_KEY"):
            create_app()

    def test_empty_jwt_secret_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            get_jwt_secret()


class TestSecretLookup:
    def test_secret_key_is_used_when_jwt_secret_key_is_unset(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        monkeypatch.setenv("SECRET_KEY", "fallback-setting")
        assert get_jwt_secret() == "fallback-setting"

    def test_jwt_secret_key_wins_over_secret_key(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "primary-setting")
        monkeypatch.setenv("SECRET_KEY", "fallback-setting")
        assert get_jwt_secret() == "primary-setting"

    def test_customer_secret_has_no_default(self, monkeypatch):
        monkeypatch.delenv("CUSTOMER_JWT_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            get_customer_jwt_secret()
