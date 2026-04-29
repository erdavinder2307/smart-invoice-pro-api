"""
validation_utils.py — shared validators and standardised API error response builder.

All API route handlers should use:
  - make_error_response(...)  to build (response, status) tuples
  - collect_errors(...)       to gather per-field validator results
  - Individual validate_*     helpers for common formats
"""

import re
from flask import jsonify

# ── Error type constants ─────────────────────────────────────────────────────
VALIDATION_ERROR = "validation_error"
BUSINESS_ERROR   = "business_error"
AUTH_ERROR       = "auth_error"
SERVER_ERROR     = "server_error"
NOT_FOUND_ERROR  = "not_found"


# ── Standardised response builder ────────────────────────────────────────────

def make_error_response(error_type, message, fields=None, status=400):
    """
    Return a Flask (response, status_code) tuple in the standard error shape:

        {
          "success": false,
          "error": {
            "type": "validation_error | business_error | auth_error | server_error",
            "message": "Short readable message",
            "fields": { "field_name": "Field-specific error" }   // optional
          }
        }
    """
    body = {
        "success": False,
        "error": {
            "type": error_type,
            "message": message,
        },
    }
    if fields:
        body["error"]["fields"] = fields
    return jsonify(body), status


# ── Per-field validators (return error string or None) ───────────────────────

def validate_required(value, label):
    """Return error message if value is empty/None, else None."""
    if value is None or str(value).strip() == "":
        return f"{label} is required"
    return None


def validate_email(email, label="Email"):
    """Return error message if email format is invalid, else None."""
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, str(email).strip()):
        return "Invalid email address"
    return None


def validate_gst(gst, label="GST Number"):
    """
    Return error message if GST format is invalid, else None.
    Pass an already-stripped value.  Returns None for empty strings (field is optional).
    """
    if not gst or not str(gst).strip():
        return None
    pattern = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(pattern, str(gst).strip().upper()):
        return "Invalid GST number format (e.g. 27AABCU9603R1ZX)"
    return None


def validate_pan(pan, label="PAN"):
    """
    Return error message if PAN format is invalid, else None.
    Returns None for empty strings (field is optional).
    """
    if not pan or not str(pan).strip():
        return None
    pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
    if not re.match(pattern, str(pan).strip().upper()):
        return "Invalid PAN format (e.g. AAACI2405N)"
    return None


def validate_mobile(mobile, label="Phone"):
    """
    Return error message if Indian mobile number is invalid, else None.
    Returns None for empty strings (field is optional).
    Strips common separators before matching.
    """
    if not mobile or not str(mobile).strip():
        return None
    stripped = re.sub(r"[\s\-\(\)\+]", "", str(mobile).strip())
    # Accept 10-digit numbers starting with 6–9
    if not re.match(r"^[6-9]\d{9}$", stripped):
        return "Invalid mobile number (10 digits starting with 6–9)"
    return None


def validate_positive_number(value, label, allow_zero=True, max_val=None):
    """
    Return error message if value is not a valid non-negative number, else None.
    allow_zero=True  → value >= 0
    allow_zero=False → value > 0
    max_val          → optional upper bound
    """
    if value is None or str(value).strip() == "":
        return f"{label} is required"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return f"{label} must be a valid number"
    if allow_zero and n < 0:
        return f"{label} cannot be negative"
    if not allow_zero and n <= 0:
        return f"{label} must be greater than 0"
    if max_val is not None and n > max_val:
        return f"{label} cannot exceed {max_val:,.2f}"
    return None


def validate_string_length(value, label, max_length):
    """Return error message if string exceeds max_length, else None."""
    if value and len(str(value)) > max_length:
        return f"{label} must be {max_length} characters or fewer"
    return None


def validate_date(value, label="Date"):
    """Return error message if value is not a valid ISO date string (YYYY-MM-DD), else None."""
    if not value or not str(value).strip():
        return f"{label} is required"
    from datetime import datetime
    try:
        datetime.strptime(str(value).strip(), "%Y-%m-%d")
    except ValueError:
        return f"{label} must be a valid date (YYYY-MM-DD)"
    return None


# ── Batch error collector ─────────────────────────────────────────────────────

def collect_errors(**kwargs):
    """
    Collect field errors from validator results.

    Usage:
        errors = collect_errors(
            vendor_name=validate_required(data.get("vendor_name"), "Vendor Name"),
            email=validate_email(data.get("email")) if data.get("email") else None,
        )
        if errors:
            return make_error_response(VALIDATION_ERROR, "Please fix the highlighted fields", errors)

    Returns a non-empty dict `{field: error_msg}` when there are errors, else None.
    """
    errors = {field: msg for field, msg in kwargs.items() if msg}
    return errors if errors else None
