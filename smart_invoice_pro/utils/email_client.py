"""One place that builds the Azure Communication Services EmailClient.

Preferred: ``AZURE_EMAIL_ENDPOINT`` (the email resource's endpoint URL) plus the
host's managed identity through ``DefaultAzureCredential`` — no key anywhere.
Locally, ``DefaultAzureCredential`` also picks up ``az login`` when that account
holds the "Communication and Email Service Owner" role on the resource.

Fallback: ``AZURE_EMAIL_CONNECTION_STRING``. Hosts that still need it should set
it as a Key Vault reference, never as a plain value.

Call ``get_email_client()`` at send time (not at import time) so settings changed
on the host take effect without a code change.
"""
import os

from azure.communication.email import EmailClient

try:
    from azure.identity import DefaultAzureCredential
except ImportError:  # pragma: no cover - azure-identity is in requirements.txt
    DefaultAzureCredential = None

_credential = None


def _endpoint():
    return (os.getenv('AZURE_EMAIL_ENDPOINT') or '').strip()


def _connection_string():
    return (os.getenv('AZURE_EMAIL_CONNECTION_STRING') or '').strip()


def email_configured():
    """True when a send can be attempted (endpoint or connection string set)."""
    return bool(_endpoint() or _connection_string())


def get_email_client():
    """Return an EmailClient, or None when email is not configured."""
    global _credential
    endpoint = _endpoint()
    if endpoint:
        if DefaultAzureCredential is None:
            raise RuntimeError(
                "AZURE_EMAIL_ENDPOINT is set but the azure-identity package is not installed"
            )
        if _credential is None:
            _credential = DefaultAzureCredential()
        return EmailClient(endpoint, _credential)
    connection_string = _connection_string()
    if connection_string:
        return EmailClient.from_connection_string(connection_string)
    return None
