"""Tests for smart_invoice_pro/utils/email_client.py – managed identity first, key fallback."""
from unittest.mock import patch

import pytest

from smart_invoice_pro.utils import email_client as ec


@pytest.fixture(autouse=True)
def _clear_email_env(monkeypatch):
    monkeypatch.delenv("AZURE_EMAIL_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_EMAIL_CONNECTION_STRING", raising=False)
    monkeypatch.setattr(ec, "_credential", None)


class TestEmailConfigured:
    def test_false_when_nothing_set(self):
        assert ec.email_configured() is False

    def test_true_with_endpoint(self, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "https://example.communication.azure.com")
        assert ec.email_configured() is True

    def test_true_with_connection_string(self, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_CONNECTION_STRING", "endpoint=https://x;accesskey=TEST")
        assert ec.email_configured() is True

    def test_blank_values_count_as_unset(self, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "   ")
        monkeypatch.setenv("AZURE_EMAIL_CONNECTION_STRING", "")
        assert ec.email_configured() is False


class TestGetEmailClient:
    def test_none_when_nothing_set(self):
        assert ec.get_email_client() is None

    @patch("smart_invoice_pro.utils.email_client.DefaultAzureCredential")
    @patch("smart_invoice_pro.utils.email_client.EmailClient")
    def test_endpoint_uses_managed_identity(self, mock_cls, mock_cred_cls, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "https://example.communication.azure.com")
        client = ec.get_email_client()
        mock_cred_cls.assert_called_once_with()
        mock_cls.assert_called_once_with(
            "https://example.communication.azure.com", mock_cred_cls.return_value
        )
        mock_cls.from_connection_string.assert_not_called()
        assert client is mock_cls.return_value

    @patch("smart_invoice_pro.utils.email_client.DefaultAzureCredential")
    @patch("smart_invoice_pro.utils.email_client.EmailClient")
    def test_credential_is_created_once(self, mock_cls, mock_cred_cls, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "https://example.communication.azure.com")
        ec.get_email_client()
        ec.get_email_client()
        assert mock_cred_cls.call_count == 1
        assert mock_cls.call_count == 2

    @patch("smart_invoice_pro.utils.email_client.DefaultAzureCredential")
    @patch("smart_invoice_pro.utils.email_client.EmailClient")
    def test_endpoint_wins_over_connection_string(self, mock_cls, mock_cred_cls, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "https://example.communication.azure.com")
        monkeypatch.setenv("AZURE_EMAIL_CONNECTION_STRING", "endpoint=https://x;accesskey=TEST")
        ec.get_email_client()
        mock_cls.from_connection_string.assert_not_called()
        mock_cls.assert_called_once()

    @patch("smart_invoice_pro.utils.email_client.DefaultAzureCredential")
    @patch("smart_invoice_pro.utils.email_client.EmailClient")
    def test_connection_string_fallback(self, mock_cls, mock_cred_cls, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_CONNECTION_STRING", "endpoint=https://x;accesskey=TEST")
        client = ec.get_email_client()
        mock_cls.from_connection_string.assert_called_once_with("endpoint=https://x;accesskey=TEST")
        mock_cred_cls.assert_not_called()
        assert client is mock_cls.from_connection_string.return_value

    @patch("smart_invoice_pro.utils.email_client.DefaultAzureCredential", None)
    @patch("smart_invoice_pro.utils.email_client.EmailClient")
    def test_endpoint_without_azure_identity_is_a_clear_error(self, mock_cls, monkeypatch):
        monkeypatch.setenv("AZURE_EMAIL_ENDPOINT", "https://example.communication.azure.com")
        with pytest.raises(RuntimeError, match="azure-identity"):
            ec.get_email_client()
        mock_cls.assert_not_called()
