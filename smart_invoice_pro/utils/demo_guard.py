"""
Demo tenant guards — settings blocks, create quotas, upload limits.

Applied when JWT has is_demo=true or tenant_id matches DEMO_TENANT_ID.
"""

from __future__ import annotations

import os
from functools import wraps

from flask import jsonify, request

from smart_invoice_pro.utils.cosmos_client import (
    customers_container,
    invoices_container,
    vendors_container,
)

DEMO_CREATE_LIMITS = {
    "customers": 20,
    "invoices": 20,
    "vendors": 20,
}

DEMO_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

_ENTITY_CONTAINERS = {
    "customers": customers_container,
    "invoices": invoices_container,
    "vendors": vendors_container,
}


def _demo_tenant_id() -> str | None:
    value = (os.getenv("DEMO_TENANT_ID") or "").strip()
    return value or None


def request_is_demo_mode() -> bool:
    if getattr(request, "is_demo", False):
        return True
    tenant_id = getattr(request, "tenant_id", None)
    demo_tid = _demo_tenant_id()
    return bool(demo_tid and tenant_id == demo_tid)


def _count_tenant_records(container, tenant_id: str) -> int:
    results = list(
        container.query_items(
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    if not results:
        return 0
    return int(results[0])


def forbid_demo_settings_mutation(message: str | None = None):
    """Block mutating organisation / platform settings in demo mode."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if request_is_demo_mode():
                return jsonify({
                    "error": message or (
                        "This setting cannot be changed in the Interactive Workspace."
                    ),
                    "code": "demo_settings_locked",
                }), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def enforce_demo_create_limit(entity: str):
    """Cap creates per entity type for demo tenants (post-seed visitor data)."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not request_is_demo_mode():
                return fn(*args, **kwargs)

            tenant_id = getattr(request, "tenant_id", None)
            if not tenant_id:
                return jsonify({"error": "Unauthorized"}), 401

            limit = DEMO_CREATE_LIMITS.get(entity, 20)
            container = _ENTITY_CONTAINERS.get(entity)
            if not container:
                return fn(*args, **kwargs)

            count = _count_tenant_records(container, tenant_id)
            if count >= limit:
                return jsonify({
                    "error": (
                        f"Interactive Workspace limit reached "
                        f"({limit} {entity}). Data resets periodically."
                    ),
                    "code": "demo_create_limit",
                    "entity": entity,
                    "limit": limit,
                }), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def demo_upload_too_large(content_length: int | None) -> bool:
    if not request_is_demo_mode():
        return False
    if content_length is None:
        return False
    return content_length > DEMO_MAX_UPLOAD_BYTES
