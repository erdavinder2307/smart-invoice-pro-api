"""Full data export (SE-4169): an Admin downloads all customers, invoices, bills and payments as CSV in one zip."""

import logging
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request

from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.cosmos_client import bills_container, customers_container, invoices_container
from smart_invoice_pro.utils.data_export import build_export_zip
from smart_invoice_pro.utils.permission_checker import require_admin

logger = logging.getLogger(__name__)

data_export_blueprint = Blueprint('data_export', __name__)


def _tenant_docs(container, tenant_id):
    return list(container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))


@data_export_blueprint.route('/export/data', methods=['GET'])
@require_admin
def export_data():
    """Zip of customers.csv, invoices.csv, bills.csv and payments.csv for the caller's tenant."""
    tenant_id = request.tenant_id
    try:
        customers = _tenant_docs(customers_container, tenant_id)
        invoices = _tenant_docs(invoices_container, tenant_id)
        bills = _tenant_docs(bills_container, tenant_id)
    except Exception as exc:
        logger.error(f"data export: query failed tenant={tenant_id}: {type(exc).__name__}")
        return jsonify({'error': 'Export failed, please try again'}), 500

    now = datetime.now(timezone.utc)
    payload = build_export_zip(customers, invoices, bills, now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    log_audit_event({
        "entity": "data_export",
        "action": "EXPORT",
        "entity_id": tenant_id,
        "risk_level": "high",
        "summary": "Full data export downloaded",
        "metadata": {"customers": len(customers), "invoices": len(invoices), "bills": len(bills)},
    })

    filename = f"solidev-books-export-{now.strftime('%Y-%m-%d')}.zip"
    return Response(
        payload,
        mimetype='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store',
        },
    )
