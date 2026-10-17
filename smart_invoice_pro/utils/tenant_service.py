"""
tenant_service.py
=================
Tenant provisioning helpers for registration and platform admin APIs.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

from smart_invoice_pro.utils.cosmos_client import tenants_container

VALID_TENANT_PLANS = frozenset({"trial", "starter", "pro", "enterprise"})
DEFAULT_TENANT_PLAN = "trial"
VALID_TENANT_STATUSES = frozenset({"active", "inactive", "suspended"})
VALID_TENANT_TYPES = frozenset({"PRODUCTION", "DEMO", "INTERNAL"})
DEFAULT_TENANT_TYPE = "PRODUCTION"
TRIAL_DAYS = 30


PARTNER_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")


def normalize_partner_code(raw) -> str | None:
    """Return the partner code in upper case, None when blank. Raises ValueError when malformed."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("partner_code must be text")
    code = raw.strip().upper()
    if not code:
        return None
    if not PARTNER_CODE_RE.match(code):
        raise ValueError("partner_code must be 3-32 letters, digits or dashes")
    return code


def get_tenant_by_id(tenant_id: str) -> dict | None:
    items = list(tenants_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id",
        parameters=[{"name": "@id", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def create_tenant_doc(
    *,
    name: str,
    tenant_id: str | None = None,
    plan: str | None = None,
    status: str = "active",
    owner_user_id: str | None = None,
    tenant_type: str | None = None,
    partner_code: str | None = None,
) -> dict:
    """Create a tenant document. Raises ValueError on invalid input."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")

    clean_plan = (plan or DEFAULT_TENANT_PLAN).strip().lower()
    if clean_plan not in VALID_TENANT_PLANS:
        raise ValueError(
            f"Invalid plan. Must be one of: {', '.join(sorted(VALID_TENANT_PLANS))}"
        )

    clean_status = (status or "active").strip().lower()
    if clean_status not in VALID_TENANT_STATUSES:
        raise ValueError(
            f"Invalid status. Must be one of: {', '.join(sorted(VALID_TENANT_STATUSES))}"
        )

    clean_type = (tenant_type or DEFAULT_TENANT_TYPE).strip().upper()
    if clean_type not in VALID_TENANT_TYPES:
        raise ValueError(
            f"Invalid tenant_type. Must be one of: {', '.join(sorted(VALID_TENANT_TYPES))}"
        )

    tid = tenant_id or str(uuid.uuid4())
    if get_tenant_by_id(tid):
        raise ValueError("Tenant already exists")

    created = datetime.utcnow()
    now = created.isoformat()
    doc = {
        "id": tid,
        "name": clean_name,
        "status": clean_status,
        "plan": clean_plan,
        "tenant_type": clean_type,
        "created_at": now,
        "updated_at": now,
    }
    if clean_plan == "trial":
        # Absolute end time, checked against the clock on every write (utils/entitlements.py).
        doc["trial_ends_at"] = (created + timedelta(days=TRIAL_DAYS)).isoformat()
    if owner_user_id:
        doc["owner_user_id"] = owner_user_id
    clean_partner_code = normalize_partner_code(partner_code)
    if clean_partner_code:
        doc["partner_code"] = clean_partner_code

    tenants_container.create_item(body=doc)
    return doc


def ensure_tenant_exists(
    tenant_id: str,
    *,
    name: str | None = None,
    owner_user_id: str | None = None,
    plan: str | None = None,
    partner_code: str | None = None,
) -> dict:
    """Idempotent tenant provisioning used during user registration."""
    existing = get_tenant_by_id(tenant_id)
    if existing:
        return existing

    fallback_name = name or f"Organization {tenant_id[:8]}"
    return create_tenant_doc(
        tenant_id=tenant_id,
        name=fallback_name,
        owner_user_id=owner_user_id,
        plan=plan or DEFAULT_TENANT_PLAN,
        status="active",
        partner_code=partner_code,
    )
