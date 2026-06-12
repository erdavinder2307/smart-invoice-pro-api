"""
tenant_service.py
=================
Tenant provisioning helpers for registration and platform admin APIs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from smart_invoice_pro.utils.cosmos_client import tenants_container

VALID_TENANT_PLANS = frozenset({"trial", "starter", "pro", "enterprise"})
DEFAULT_TENANT_PLAN = "trial"
VALID_TENANT_STATUSES = frozenset({"active", "inactive", "suspended"})


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


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

    tid = tenant_id or str(uuid.uuid4())
    if get_tenant_by_id(tid):
        raise ValueError("Tenant already exists")

    now = _now_iso()
    doc = {
        "id": tid,
        "name": clean_name,
        "status": clean_status,
        "plan": clean_plan,
        "created_at": now,
        "updated_at": now,
    }
    if owner_user_id:
        doc["owner_user_id"] = owner_user_id

    tenants_container.create_item(body=doc)
    return doc


def ensure_tenant_exists(
    tenant_id: str,
    *,
    name: str | None = None,
    owner_user_id: str | None = None,
    plan: str | None = None,
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
    )
