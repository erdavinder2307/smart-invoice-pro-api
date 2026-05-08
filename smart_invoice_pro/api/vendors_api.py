from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import vendors_container, bills_container
from smart_invoice_pro.utils.validation_utils import (
    make_error_response, collect_errors,
    validate_required, validate_email, validate_gst, validate_mobile,
    VALIDATION_ERROR, NOT_FOUND_ERROR, SERVER_ERROR,
)
import uuid
from flasgger import swag_from
from datetime import datetime
from smart_invoice_pro.utils.audit_logger import log_audit_event
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity

vendors_blueprint = Blueprint('vendors', __name__)

_ALLOWED_SORT_FIELDS = {
    'vendor_name',
    'total_purchases',
    'outstanding_amount',
    'last_transaction_date',
    'payment_terms',
    'status',
    'created_at',
}


def _validate_vendor(data, is_update=False):
    """
    Validate vendor payload.  Returns a dict of {field: error_msg} or None.
    On CREATE all required fields must be present; on UPDATE only provided
    fields are validated.
    """
    vendor_name = data.get('vendor_name', '').strip()
    email       = data.get('email', '').strip()
    phone       = data.get('phone', '').strip()
    gst_number  = data.get('gst_number', '').strip()

    return collect_errors(
        vendor_name=validate_required(vendor_name, 'Vendor Name') if not is_update else None,
        email=validate_email(email) if email else None,
        phone=validate_mobile(phone) if phone else None,
        gst_number=validate_gst(gst_number) if gst_number else None,
    )


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def _sanitize_vendor(vendor):
    sanitized = {k: v for k, v in vendor.items() if not str(k).startswith('_')}
    if not sanitized.get('vendor_name'):
        sanitized['vendor_name'] = (
            sanitized.get('name')
            or sanitized.get('company_name')
            or sanitized.get('display_name')
            or ''
        )
    return sanitized


def _query_vendor(vendor_id, tenant_id):
    query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
    rows = list(vendors_container.query_items(
        query=query,
        parameters=[
            {"name": "@id", "value": vendor_id},
            {"name": "@tenant_id", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return rows[0] if rows else None


def _aggregate_vendor_metrics(tenant_id):
    query = (
        "SELECT c.vendor_id, c.total_amount, c.balance_due, c.bill_date, c.created_at "
        "FROM c WHERE c.tenant_id = @tenant_id"
    )
    bills = list(bills_container.query_items(
        query=query,
        parameters=[{"name": "@tenant_id", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))

    metrics = {}
    for bill in bills:
        vendor_id = bill.get('vendor_id')
        if not vendor_id:
            continue

        bucket = metrics.setdefault(vendor_id, {
            'total_purchases': 0.0,
            'outstanding_amount': 0.0,
            'last_transaction_date': None,
        })
        bucket['total_purchases'] += _to_float(bill.get('total_amount', 0.0))
        bucket['outstanding_amount'] += _to_float(bill.get('balance_due', 0.0))

        tx_date = _parse_date(bill.get('bill_date') or bill.get('created_at'))
        if tx_date and (bucket['last_transaction_date'] is None or tx_date > bucket['last_transaction_date']):
            bucket['last_transaction_date'] = tx_date

    for vendor_id, item in metrics.items():
        if item['last_transaction_date']:
            item['last_transaction_date'] = item['last_transaction_date'].date().isoformat()

    return metrics


def _sort_vendor_items(items, sort_by, sort_order):
    reverse = sort_order == 'DESC'

    if sort_by in {'total_purchases', 'outstanding_amount'}:
        return sorted(items, key=lambda v: _to_float(v.get(sort_by, 0.0)), reverse=reverse)

    if sort_by == 'last_transaction_date':
        return sorted(
            items,
            key=lambda v: _parse_date(v.get('last_transaction_date')) or datetime.min,
            reverse=reverse,
        )

    return sorted(items, key=lambda v: str(v.get(sort_by, '')).lower(), reverse=reverse)


def _is_archived(vendor):
    return str(vendor.get('status', '')).upper() == 'ARCHIVED'

@vendors_blueprint.route('/vendors', methods=['POST'])
@swag_from({
    'tags': ['Vendors'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'contact_person': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'address': {'type': 'string'},
                    'city': {'type': 'string'},
                    'state': {'type': 'string'},
                    'postal_code': {'type': 'string'},
                    'country': {'type': 'string'},
                    'tax_id': {'type': 'string'},
                    'payment_terms': {'type': 'string'},
                    'notes': {'type': 'string'}
                },
                'required': ['name', 'contact_person']
            },
            'description': 'Vendor data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Vendor created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'name': 'ABC Suppliers',
                    'contact_person': 'John Doe'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_vendor():
    """Create a new vendor"""
    data = request.get_json() or {}

    errors = _validate_vendor(data, is_update=False)
    if errors:
        return make_error_response(
            VALIDATION_ERROR, "Please fix the highlighted fields", errors
        )

    now = datetime.utcnow().isoformat()
    vendor_id = str(uuid.uuid4())

    item = {
        'id': vendor_id,
        'vendor_id': vendor_id,
        'vendor_name': data.get('vendor_name', '').strip(),
        'contact_person': data.get('contact_person', '').strip(),
        'email': data.get('email', '').strip(),
        'phone': data.get('phone', '').strip(),
        'address': data.get('address', '').strip(),
        'gst_number': data.get('gst_number', '').strip().upper(),
        'payment_terms': data.get('payment_terms', 'Net 30'),
        'status': 'ACTIVE',
        'archived_at': None,
        'archived_by': None,
        'notes': data.get('notes', '').strip(),
        'tenant_id': request.tenant_id,
        'created_at': now,
        'updated_at': now,
    }

    try:
        created_item = vendors_container.create_item(body=item)
        log_audit_event({
            "action": "CREATE",
            "entity": "vendor",
            "entity_id": vendor_id,
            "before": None,
            "after": created_item,
            "metadata": {"event": "create_vendor"},
            "tenant_id": request.tenant_id,
            "user_id": getattr(request, "user_id", None),
        })
        return jsonify(created_item), 201
    except Exception as e:
        return make_error_response(SERVER_ERROR, "Failed to create vendor", status=500)

@vendors_blueprint.route('/vendors', methods=['GET'])
@swag_from({
    'tags': ['Vendors'],
    'parameters': [
        {
            'name': 'search',
            'in': 'query',
            'type': 'string',
            'description': 'Search by vendor name'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of vendors',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_vendors():
    """Get all vendors with optional search"""
    try:
        tenant_id = request.tenant_id
        search_term = (request.args.get('q') or request.args.get('search') or '').strip().lower()
        status_filter = (request.args.get('status') or '').strip()
        lifecycle = str(request.args.get('lifecycle', 'active')).strip().lower()
        outstanding_filter = (request.args.get('outstanding') or '').strip().lower()
        payment_terms_filter = (request.args.get('payment_terms') or '').strip()
        include_meta = str(request.args.get('include_meta', '')).lower() in ('1', 'true', 'yes')

        sort_by = request.args.get('sort_by', 'vendor_name')
        sort_order = request.args.get('sort_order', 'asc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'vendor_name'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'ASC'

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        try:
            limit = int(request.args.get('page_size', request.args.get('limit', 10)))
        except ValueError:
            limit = 10
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit

        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        params = [{"name": "@tenant_id", "value": tenant_id}]
        if search_term:
            query += (
                " AND (CONTAINS(LOWER(c.vendor_name), @search)"
                " OR CONTAINS(LOWER(c.name), @search)"
                " OR CONTAINS(LOWER(c.contact_person), @search)"
                " OR CONTAINS(LOWER(c.email), @search)"
                " OR CONTAINS(LOWER(c.phone), @search))"
            )
            params.append({"name": "@search", "value": search_term.lower()})

        if status_filter:
            status_filter_lower = status_filter.lower()
            if status_filter_lower == 'active':
                query += (
                    " AND (NOT IS_DEFINED(c.status) OR IS_NULL(c.status)"
                    " OR LOWER(c.status) = @status_lower)"
                )
            else:
                query += " AND LOWER(c.status) = @status_lower"
            params.append({"name": "@status_lower", "value": status_filter_lower})

        if payment_terms_filter:
            query += " AND c.payment_terms = @payment_terms"
            params.append({"name": "@payment_terms", "value": payment_terms_filter})

        query += " ORDER BY c.vendor_name ASC"

        rows = list(vendors_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))

        if lifecycle == 'archived':
            rows = [row for row in rows if _is_archived(row)]
        elif lifecycle == 'all':
            rows = list(rows)
        else:
            rows = [row for row in rows if not _is_archived(row)]

        metrics = _aggregate_vendor_metrics(tenant_id)
        enriched = []
        for row in rows:
            vendor = _sanitize_vendor(row)
            vendor_metrics = metrics.get(vendor.get('id'), {})
            vendor['total_purchases'] = _to_float(vendor_metrics.get('total_purchases', 0.0))
            vendor['outstanding_amount'] = _to_float(vendor_metrics.get('outstanding_amount', 0.0))
            vendor['last_transaction_date'] = vendor_metrics.get('last_transaction_date')
            enriched.append(vendor)

        if outstanding_filter == 'with_payables':
            enriched = [item for item in enriched if _to_float(item.get('outstanding_amount', 0.0)) > 0]
        elif outstanding_filter == 'cleared':
            enriched = [item for item in enriched if _to_float(item.get('outstanding_amount', 0.0)) <= 0]

        sorted_items = _sort_vendor_items(enriched, sort_by, sort_order)
        total = len(sorted_items)

        if not include_meta and 'page' not in request.args and 'page_size' not in request.args and 'limit' not in request.args:
            return jsonify(sorted_items), 200

        paged = sorted_items[offset:offset + limit]

        summary = {
            'total_vendors': total,
            'active_vendors': sum(
                1
                for item in sorted_items
                if not item.get('status') or str(item.get('status', '')).lower() == 'active'
            ),
            'vendors_with_payables': sum(1 for item in sorted_items if _to_float(item.get('outstanding_amount', 0.0)) > 0),
            'high_outstanding_vendors': sum(1 for item in sorted_items if _to_float(item.get('outstanding_amount', 0.0)) >= 50000),
        }

        return jsonify({
            'data': paged,
            'total': total,
            'page': page,
            'limit': limit,
            'summary': summary,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve vendors: {str(e)}"}), 500


@vendors_blueprint.route('/vendors/bulk', methods=['POST'])
@vendors_blueprint.route('/vendors/bulk-archive', methods=['POST'])
def bulk_vendor_actions():
    payload = request.get_json() or {}
    action = str(payload.get('action', '')).strip().lower()
    ids = payload.get('ids') or []

    if action not in {'delete', 'mark_inactive', 'archive'}:
        return jsonify({'error': 'Invalid bulk action'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty array'}), 400

    deleted = 0
    updated = 0
    errors = []

    for vendor_id in ids:
        try:
            vendor = _query_vendor(vendor_id, request.tenant_id)
            if not vendor:
                errors.append({'id': vendor_id, 'error': 'not found'})
                continue

            if action in {'delete', 'archive'}:
                archive_entity(
                    vendors_container,
                    vendor,
                    'vendor',
                    request.tenant_id,
                    user_id=getattr(request, 'user_id', None),
                    reason='Bulk archive',
                )
                deleted += 1
                continue

            vendor['status'] = 'Inactive'
            vendor['updated_at'] = datetime.utcnow().isoformat()
            vendors_container.replace_item(item=vendor['id'], body=vendor)
            updated += 1
        except Exception as exc:
            errors.append({'id': vendor_id, 'error': str(exc)})

    return jsonify({
        'action': action,
        'processed': len(ids),
        'deleted': deleted,
        'updated': updated,
        'errors': errors,
    }), 200

@vendors_blueprint.route('/vendors/<vendor_id>', methods=['GET'])
@swag_from({
    'tags': ['Vendors'],
    'parameters': [
        {
            'name': 'vendor_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Vendor ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Vendor retrieved successfully'
        },
        '404': {
            'description': 'Vendor not found'
        }
    }
})
def get_vendor(vendor_id):
    """Get a vendor by ID"""
    try:
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(vendors_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": vendor_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Vendor not found"}), 404

        if _is_archived(items[0]):
            return jsonify({"error": "Vendor not found"}), 404

        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve vendor: {str(e)}"}), 500

@vendors_blueprint.route('/vendors/<vendor_id>', methods=['PUT'])
@swag_from({
    'tags': ['Vendors'],
    'parameters': [
        {
            'name': 'vendor_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Vendor ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'contact_person': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'address': {'type': 'string'},
                    'payment_terms': {'type': 'string'},
                    'notes': {'type': 'string'}
                }
            },
            'description': 'Updated vendor data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Vendor updated successfully'
        },
        '404': {
            'description': 'Vendor not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def update_vendor(vendor_id):
    """Update a vendor"""
    data = request.get_json() or {}

    errors = _validate_vendor(data, is_update=True)
    if errors:
        return make_error_response(
            VALIDATION_ERROR, "Please fix the highlighted fields", errors
        )

    try:
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(vendors_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": vendor_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))

        if not items:
            return make_error_response(NOT_FOUND_ERROR, "Vendor not found", status=404)

        vendor = items[0]
        if _is_archived(vendor):
            return make_error_response(NOT_FOUND_ERROR, "Vendor not found", status=404)
        before_snapshot = dict(vendor)

        updatable_fields = [
            'vendor_name', 'contact_person', 'email', 'phone',
            'address', 'gst_number', 'payment_terms', 'status', 'notes',
        ]
        for field in updatable_fields:
            if field in data:
                value = data[field]
                if field == 'gst_number' and value:
                    value = value.strip().upper()
                vendor[field] = value

        vendor['updated_at'] = datetime.utcnow().isoformat()

        updated_item = vendors_container.replace_item(item=vendor['id'], body=vendor)

        log_audit_event({
            "action": "UPDATE",
            "entity": "vendor",
            "entity_id": vendor_id,
            "before": before_snapshot,
            "after": updated_item,
            "metadata": {"event": "update_vendor"},
            "tenant_id": request.tenant_id,
            "user_id": getattr(request, "user_id", None),
        })

        return jsonify(updated_item), 200
    except Exception as e:
        return make_error_response(SERVER_ERROR, "Failed to update vendor", status=500)

@vendors_blueprint.route('/vendors/<vendor_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Vendors'],
    'parameters': [
        {
            'name': 'vendor_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Vendor ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Vendor deleted successfully'
        },
        '404': {
            'description': 'Vendor not found'
        }
    }
})
def delete_vendor(vendor_id):
    """Delete a vendor"""
    try:
        # Fetch the vendor to get partition key
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(vendors_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": vendor_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Vendor not found"}), 404
        
        vendor = items[0]
        if _is_archived(vendor):
            return jsonify({"error": "Vendor not found"}), 404

        dependency = check_entity_dependencies('vendor', vendor_id, request.tenant_id)
        archived_vendor = archive_entity(
            vendors_container,
            vendor,
            'vendor',
            request.tenant_id,
            user_id=getattr(request, 'user_id', None),
            reason='User requested archive from delete action',
        )

        return jsonify({
            "message": "Vendor archived successfully",
            "status": archived_vendor.get("status"),
            "dependencySummary": dependency.get("dependencySummary", {}),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete vendor: {str(e)}"}), 500


        return jsonify({
            "message": "Vendor archived successfully",
            "status": archived_vendor.get("status"),
            "dependencySummary": dependency.get("dependencySummary", {}),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete vendor: {str(e)}"}), 500


@vendors_blueprint.route('/vendors/<vendor_id>/restore', methods=['POST'])
def restore_vendor(vendor_id):
    """Restore an archived vendor back to ACTIVE status."""
    items = list(vendors_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
        parameters=[
            {"name": "@id", "value": vendor_id},
            {"name": "@tenant_id", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({'error': 'Vendor not found'}), 404
    item = items[0]
    if not _is_archived(item):
        return jsonify({'error': 'Vendor is not archived'}), 422
    restored = restore_entity(
        vendors_container, item, 'vendor', request.tenant_id,
        user_id=getattr(request, 'user_id', None), reason='User requested restore',
    )
    return jsonify({'message': 'Vendor restored', 'status': restored.get('status')}), 200


@vendors_blueprint.route('/vendors/<vendor_id>/dependencies', methods=['GET'])
def get_vendor_dependencies(vendor_id):
    vendor = _query_vendor(vendor_id, request.tenant_id)
    if not vendor:
        return jsonify({'error': 'Vendor not found'}), 404

    dependency = check_entity_dependencies('vendor', vendor_id, request.tenant_id)
    return jsonify(dependency), 200
