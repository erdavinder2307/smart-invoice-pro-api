"""Shared stock ledger helpers for invoices and stock API."""
from smart_invoice_pro.utils.cosmos_client import stock_container, products_container


def product_exists_for_tenant(product_id, tenant_id):
    rows = list(products_container.query_items(
        query=(
            "SELECT TOP 1 c.id, c.is_deleted FROM c "
            "WHERE c.id = @id AND c.tenant_id = @tenant_id"
        ),
        parameters=[
            {"name": "@id", "value": str(product_id)},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return bool(rows) and not rows[0].get('is_deleted', False)


def compute_current_stock(product_id, tenant_id):
    items = list(stock_container.query_items(
        query=(
            "SELECT c.type, c.quantity FROM c "
            "WHERE c.product_id = @product_id AND c.tenant_id = @tenant_id"
        ),
        parameters=[
            {"name": "@product_id", "value": str(product_id)},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    stock_in = sum(float(item.get('quantity', 0)) for item in items if item.get('type') == 'IN')
    stock_out = sum(float(item.get('quantity', 0)) for item in items if item.get('type') == 'OUT')
    return stock_in - stock_out


def validate_stock_out(items, tenant_id, credit_items=None):
    """
    Validate that OUT quantities can be fulfilled.
    credit_items: optional IN reversal lines to virtually add back before checking OUT.
    Returns (None, None) on success or (error_message, details_dict) on failure.
    """
    virtual_credit = {}
    for item in credit_items or []:
        product_id = item.get('product_id')
        if not product_id or not item.get('quantity'):
            continue
        pid = str(product_id)
        virtual_credit[pid] = virtual_credit.get(pid, 0.0) + float(item['quantity'])

    details = {}
    for inv_item in items or []:
        product_id = inv_item.get('product_id')
        if not product_id or not inv_item.get('quantity'):
            continue
        pid = str(product_id)
        qty = float(inv_item['quantity'])
        available = compute_current_stock(pid, tenant_id) + virtual_credit.get(pid, 0.0)
        if available - qty < 0:
            name = inv_item.get('name') or inv_item.get('description') or pid
            details[pid] = (
                f'Insufficient stock for "{name}". Available: {available:g}, requested: {qty:g}'
            )
        virtual_credit[pid] = virtual_credit.get(pid, 0.0) - qty

    if details:
        first_msg = next(iter(details.values()))
        return first_msg, details
    return None, None
