"""
Tests for GST API — GSTIN validation & prefill.
No database containers — pure stateless endpoints.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestValidateGstin:
    """GET /api/gst/validate/<gstin>"""

    def test_valid_gstin_format(self, client, headers_a):
        resp = client.get("/api/gst/validate/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["gstin"] == "07AAACB1234F1Z5"

    def test_invalid_gstin_format(self, client, headers_a):
        resp = client.get("/api/gst/validate/INVALID123", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is False

    def test_lowercase_normalised(self, client, headers_a):
        resp = client.get("/api/gst/validate/07aaacb1234f1z5", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gstin"] == "07AAACB1234F1Z5"

    def test_empty_gstin(self, client, headers_a):
        resp = client.get("/api/gst/validate/   ", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is False


class TestPrefillGstDetails:
    """GET /api/gst/prefill/<gstin>"""

    def test_invalid_gstin_returns_400(self, client, headers_a):
        resp = client.get("/api/gst/prefill/BAD", headers=headers_a)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_mock_fallback_returns_demo_data(self, client, headers_a):
        """When GST_API_KEY is empty, returns mock demo data."""
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", ""):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "Demo" in data["data"]["legal_name"]
        assert data["data"]["state"] == "Delhi"

    def test_api_call_success(self, client, headers_a):
        """When GST_API_KEY is set, hits external API."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "lgnm": "Test Pvt Ltd",
            "tradeNam": "Test Trading",
            "dty": "Regular",
            "pradr": {"addr": {"st": "Delhi", "pncd": "110001"}},
        }
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", "test-key"), \
             patch("smart_invoice_pro.api.gst_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["data"]["legal_name"] == "Test Pvt Ltd"

    def test_api_404(self, client, headers_a):
        """External API returns 404 → GSTIN not found."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", "test-key"), \
             patch("smart_invoice_pro.api.gst_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 404

    def test_api_500(self, client, headers_a):
        """External API returns server error → 500."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", "test-key"), \
             patch("smart_invoice_pro.api.gst_api.requests.get", return_value=mock_resp):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 500

    def test_api_timeout(self, client, headers_a):
        """External API times out → 500."""
        import requests as real_requests
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", "test-key"), \
             patch("smart_invoice_pro.api.gst_api.requests.get",
                   side_effect=real_requests.exceptions.Timeout("timeout")):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.status_code == 500
        assert "timeout" in resp.get_json()["error"].lower()

    def test_state_extraction_delhi(self, client, headers_a):
        """GSTIN starting with 07 → Delhi."""
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", ""):
            resp = client.get("/api/gst/prefill/07AAACB1234F1Z5", headers=headers_a)
        assert resp.get_json()["data"]["state"] == "Delhi"

    def test_state_extraction_maharashtra(self, client, headers_a):
        """GSTIN starting with 27 → Maharashtra."""
        with patch("smart_invoice_pro.api.gst_api.GST_API_KEY", ""):
            resp = client.get("/api/gst/prefill/27AAACB1234F1Z5", headers=headers_a)
        assert resp.get_json()["data"]["state"] == "Maharashtra"
