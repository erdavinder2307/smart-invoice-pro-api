from smart_invoice_pro.utils.cosmos_client import (
    invoices_container,
    quotes_container,
    sales_orders_container,
    purchase_orders_container,
    bills_container,
    expenses_container,
    recurring_profiles_container,
    users_container,
)


def _count_value(container, query, parameters):
    try:
        result = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        ))
        return int(result[0] or 0) if result else 0
    except Exception:
        return 0


def _prune_summary(summary):
    return {k: v for k, v in summary.items() if int(v or 0) > 0}


def _check_product_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "invoices": _count_value(
            invoices_container,
            "SELECT VALUE COUNT(1) FROM c JOIN item IN c.items "
            "WHERE item.product_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "quotes": _count_value(
            quotes_container,
            "SELECT VALUE COUNT(1) FROM c JOIN item IN c.items "
            "WHERE item.product_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "sales_orders": _count_value(
            sales_orders_container,
            "SELECT VALUE COUNT(1) FROM c JOIN item IN c.items "
            "WHERE item.product_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "purchase_orders": _count_value(
            purchase_orders_container,
            "SELECT VALUE COUNT(1) FROM c JOIN item IN c.items "
            "WHERE item.product_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_customer_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "invoices": _count_value(
            invoices_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.customer_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "quotes": _count_value(
            quotes_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.customer_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "sales_orders": _count_value(
            sales_orders_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.customer_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "bills": _count_value(
            bills_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.customer_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_vendor_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "purchase_orders": _count_value(
            purchase_orders_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.vendor_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "bills": _count_value(
            bills_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.vendor_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_quote_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "invoices": _count_value(
            invoices_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.converted_from_quote_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "sales_orders": _count_value(
            sales_orders_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.converted_from_quote_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_invoice_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "sales_orders": _count_value(
            sales_orders_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.converted_to_invoice_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_sales_order_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "invoices": _count_value(
            invoices_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.converted_from_so_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_recurring_profile_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "invoices": _count_value(
            invoices_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.recurring_profile_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_bank_account_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "expenses": _count_value(
            expenses_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.bank_account_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
        "bills": _count_value(
            bills_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.bank_account_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def _check_role_dependencies(entity_id, tenant_id):
    params = [
        {"name": "@id", "value": entity_id},
        {"name": "@tenant_id", "value": tenant_id},
    ]
    summary = {
        "users": _count_value(
            users_container,
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.role_id = @id AND c.tenant_id = @tenant_id",
            params,
        ),
    }
    summary = _prune_summary(summary)
    return {
        "hasDependencies": bool(summary),
        "dependencySummary": summary,
    }


def check_entity_dependencies(entity_type, entity_id, tenant_id):
    normalized = str(entity_type or "").strip().lower()

    if normalized in {"product", "products", "item", "items"}:
        return _check_product_dependencies(entity_id, tenant_id)

    if normalized in {"customer", "customers"}:
        return _check_customer_dependencies(entity_id, tenant_id)

    if normalized in {"vendor", "vendors"}:
        return _check_vendor_dependencies(entity_id, tenant_id)

    if normalized in {"quote", "quotes"}:
        return _check_quote_dependencies(entity_id, tenant_id)

    if normalized in {"invoice", "invoices"}:
        return _check_invoice_dependencies(entity_id, tenant_id)

    if normalized in {"sales_order", "sales-orders", "sales_orders", "salesorder", "salesorders"}:
        return _check_sales_order_dependencies(entity_id, tenant_id)

    # Bills have no downstream dependents in the current model
    if normalized in {"bill", "bills"}:
        return {"hasDependencies": False, "dependencySummary": {}}

    # Purchase orders may be converted to bills — no blocking dependents tracked
    if normalized in {"purchase_order", "purchase-orders", "purchase_orders", "purchaseorder", "purchaseorders"}:
        return {"hasDependencies": False, "dependencySummary": {}}

    # Expenses are financial records and treated as accounting-protected.
    if normalized in {"expense", "expenses"}:
        return {"hasDependencies": False, "dependencySummary": {}}

    if normalized in {"recurring_profile", "recurring_profiles", "recurringprofile", "recurring-invoices", "recurring_invoices"}:
        return _check_recurring_profile_dependencies(entity_id, tenant_id)

    if normalized in {"bank_account", "bank_accounts", "bankaccount", "bank-accounts"}:
        return _check_bank_account_dependencies(entity_id, tenant_id)

    if normalized in {"role", "roles"}:
        return _check_role_dependencies(entity_id, tenant_id)

    if normalized in {"user", "users", "tax", "taxes", "tax_rate", "tax_rates", "payment", "payments", "reconciliation", "audit_log", "audit_logs"}:
        return {"hasDependencies": False, "dependencySummary": {}}

    return {
        "hasDependencies": False,
        "dependencySummary": {},
    }
