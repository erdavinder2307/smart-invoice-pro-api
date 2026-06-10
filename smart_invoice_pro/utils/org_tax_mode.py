"""
org_tax_mode.py
===============
Single source of truth for a tenant's GST registration mode.

gst_mode values
---------------
  FULL_GST    – Regular taxpayer. Full CGST/SGST/IGST on sales.
  COMPOSITION – Composition scheme. GSTIN required but no tax collected on sales.
  NO_GST      – Unregistered. No GSTIN, no tax on any document.

Call get_org_gst_mode(tenant_id) from any API or service that needs to
enforce tax behaviour. The result is intentionally NOT cached — it must
always reflect the current org setting to be legally safe.
"""

from __future__ import annotations

from smart_invoice_pro.utils.cosmos_client import settings_container as _settings_container

FULL_GST = "FULL_GST"
COMPOSITION = "COMPOSITION"
NO_GST = "NO_GST"

_VALID_MODES = {FULL_GST, COMPOSITION, NO_GST}

_REG_TYPE_TO_MODE = {
    "regular":     FULL_GST,
    "composition": COMPOSITION,
    "unregistered": NO_GST,
}


def get_org_gst_mode(tenant_id: str) -> str:
    """
    Return the gst_mode for the given tenant.

    Lookup order:
      1. ``gst_mode`` field on org profile (written by migration + settings update)
      2. Derived from ``gst_registration_type``
      3. Derived from legacy ``gst_enabled`` boolean
      4. Default: FULL_GST (fail-safe for existing Regular tenants)
    """
    doc_id = f"{tenant_id}:organization_profile"
    items = list(_settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return FULL_GST

    profile = items[0]

    # 1. Explicit gst_mode field (post-migration)
    mode = (profile.get("gst_mode") or "").strip().upper()
    if mode in _VALID_MODES:
        return mode

    # 2. Derive from gst_registration_type
    reg_type = (profile.get("gst_registration_type") or "").strip().lower()
    if reg_type in _REG_TYPE_TO_MODE:
        return _REG_TYPE_TO_MODE[reg_type]

    # 3. Legacy fallback: gst_enabled boolean
    if profile.get("gst_enabled") is False:
        return NO_GST

    return FULL_GST


def is_gst_active(tenant_id: str) -> bool:
    """Returns True for FULL_GST and COMPOSITION (GST-registered tenants)."""
    return get_org_gst_mode(tenant_id) != NO_GST


def must_suppress_sales_tax(tenant_id: str) -> bool:
    """
    Returns True when the org must NOT charge GST on sales documents.
    True for COMPOSITION and NO_GST.
    """
    return get_org_gst_mode(tenant_id) in (COMPOSITION, NO_GST)


def derive_gst_mode(gst_registration_type: str, gst_enabled: bool | None = None) -> str:
    """
    Pure function: derive gst_mode from registration type + legacy enabled flag.
    Used by the migration script and the settings update endpoint.
    """
    reg_type = (gst_registration_type or "").strip().lower()
    if reg_type == "unregistered":
        return NO_GST
    if reg_type == "composition":
        return COMPOSITION
    if reg_type == "regular":
        return FULL_GST
    # No recognised reg_type — fall back to legacy bool
    if gst_enabled is False:
        return NO_GST
    return FULL_GST
