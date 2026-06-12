"""Shared Cosmos query builder for audit / activity log reads."""

from flask import request


def parse_audit_filters(*, tenant_id=None, args=None):
    """Build WHERE conditions and parameters from query args."""
    source = args if args is not None else request.args
    scoped_tenant = tenant_id if tenant_id is not None else getattr(request, "tenant_id", None)

    entity_type = (source.get("entity_type") or "").strip()
    entity = (source.get("entity") or "").strip() or entity_type
    entity_id = (source.get("entity_id") or "").strip()
    user_id = (source.get("user_id") or "").strip()
    action = (source.get("action") or "").strip().upper()
    category = (source.get("category") or "").strip().lower()
    risk_level = (source.get("risk_level") or "").strip().lower()
    module = (source.get("module") or "").strip().lower()
    from_date = (source.get("from_date") or source.get("start_date") or "").strip()
    to_date = (source.get("to_date") or source.get("end_date") or "").strip()
    search = (source.get("search") or "").strip().lower()

    conditions = []
    params = []

    if scoped_tenant:
        conditions.append("c.tenant_id = @tid")
        params.append({"name": "@tid", "value": scoped_tenant})
    else:
        conditions.append("1=1")

    if entity:
        conditions.append("(c.entity = @entity OR c.entity_type = @entity)")
        params.append({"name": "@entity", "value": entity.lower()})

    if entity_id:
        conditions.append("c.entity_id = @entity_id")
        params.append({"name": "@entity_id", "value": entity_id})

    if user_id:
        conditions.append("c.user_id = @filter_user_id")
        params.append({"name": "@filter_user_id", "value": user_id})

    if action:
        conditions.append("UPPER(c.action) = @action")
        params.append({"name": "@action", "value": action})

    if category:
        conditions.append("c.category = @category")
        params.append({"name": "@category", "value": category})

    if risk_level:
        conditions.append("c.risk_level = @risk_level")
        params.append({"name": "@risk_level", "value": risk_level})

    if module:
        conditions.append("(c.module = @module OR c.entity = @module OR c.entity_type = @module)")
        params.append({"name": "@module", "value": module})

    if from_date:
        conditions.append("(c.created_at >= @from_date OR c.timestamp >= @from_date)")
        params.append({"name": "@from_date", "value": from_date})

    if to_date:
        conditions.append("(c.created_at <= @to_date OR c.timestamp <= @to_date)")
        params.append({"name": "@to_date", "value": to_date + "T23:59:59"})

    if search:
        conditions.append(
            "(CONTAINS(LOWER(c.entity_id), @search) "
            "OR CONTAINS(LOWER(c.user_id), @search) "
            "OR CONTAINS(LOWER(c.user_email), @search) "
            "OR CONTAINS(LOWER(c.user_name), @search) "
            "OR CONTAINS(LOWER(c.entity_label), @search) "
            "OR CONTAINS(LOWER(c.summary), @search))"
        )
        params.append({"name": "@search", "value": search})

    return conditions, params


def parse_pagination(*, args=None, default_limit=50, max_limit=200):
    source = args if args is not None else request.args
    try:
        page = max(0, int(source.get("page", 0)))
        limit = min(max_limit, max(1, int(source.get("limit", default_limit))))
    except ValueError:
        page, limit = 0, default_limit
    return page, limit
