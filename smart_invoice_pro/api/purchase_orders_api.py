from flask import Blueprint, request, jsonify, make_response
from smart_invoice_pro.utils.permission_checker import require_permission
from smart_invoice_pro.utils.cosmos_client import purchase_orders_container, bills_container
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity
from smart_invoice_pro.utils.lifecycle_service import apply_lifecycle_action
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
import uuid
import base64
from flasgger import swag_from
from datetime import datetime, timedelta
from enum import Enum
from smart_invoice_pro.api.invoice_generation import build_invoice_pdf, _get_tenant_branding, branding_for_document
from smart_invoice_pro.utils.audit_logger import log_audit, log_audit_event
import copy

purchase_orders_blueprint = Blueprint('purchase_orders', __name__)


def _is_archived(item):
    return str(item.get('lifecycle_status') or item.get('status') or '').upper() == 'ARCHIVED'

class POStatus(Enum):
    Draft = 'Draft'
    Issued = 'Issued'
    Sent = 'Sent'
    Confirmed = 'Confirmed'
    Received = 'Received'
    Billed = 'Billed'
    Closed = 'Closed'
    Cancelled = 'Cancelled'


def _normalize_po_status(raw_status):
    status = str(raw_status or '').strip()
    if not status:
        return 'Draft'
    if status in ('Sent', 'Confirmed', 'Issued'):
        return 'Issued'
    return status


def _to_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _compute_po_financials(po):
    total_amount = _to_float(po.get('total_amount', 0))
    amount_paid = po.get('amount_paid', po.get('received_amount', None))
    amount_paid = _to_float(amount_paid, 0.0)

    if amount_paid == 0.0 and _normalize_po_status(po.get('status')) in ('Received', 'Billed', 'Closed'):
        amount_paid = total_amount

    balance_due = po.get('balance_due', po.get('pending_amount', None))
    if balance_due is None:
        balance_due = max(total_amount - amount_paid, 0.0)
    else:
        balance_due = _to_float(balance_due, 0.0)

    return {
        **po,
        'status_display': _normalize_po_status(po.get('status')),
        'amount_paid': amount_paid,
        'balance_due': balance_due,
    }

def validate_po_data(data, is_update=False):
    """Validate purchase order data"""
    errors = {}
    
    if not is_update:
        required_fields = ['po_number', 'vendor_id', 'order_date', 'total_amount', 'status']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate status
    if 'status' in data and _normalize_po_status(data['status']) not in POStatus._value2member_map_:
        errors['status'] = f'Invalid status: {data["status"]}'
    
    # Validate dates
    if 'order_date' in data and 'delivery_date' in data and data['delivery_date']:
        try:
            order = datetime.fromisoformat(data['order_date'].replace('Z', '+00:00'))
            delivery = datetime.fromisoformat(data['delivery_date'].replace('Z', '+00:00'))
            if delivery < order:
                errors['delivery_date'] = 'Delivery date cannot be before order date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    from smart_invoice_pro.utils.line_item_validation import validate_line_item_rates
    errors.update(validate_line_item_rates(data.get('items')))

    return errors

@purchase_orders_blueprint.route('/purchase-orders', methods=['POST'])
@require_permission('purchase_orders', 'create')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'po_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string', 'enum': ['Draft', 'Sent', 'Confirmed', 'Received', 'Billed', 'Closed', 'Cancelled']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'items': {'type': 'array'},
                    'converted_to_bill_id': {'type': 'string'}
                },
                'required': ['po_number', 'vendor_id', 'order_date', 'total_amount', 'status']
            },
            'description': 'Purchase Order data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Purchase Order created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'po_number': 'PO-001',
                    'vendor_id': '123',
                    'status': 'Draft'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_purchase_order():
    """Create a new purchase order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_po_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    item = {
        'id': str(uuid.uuid4()),
        'po_number': data['po_number'],
        'vendor_id': data['vendor_id'],
        'vendor_name': data.get('vendor_name', ''),
        'order_date': data['order_date'],
        'delivery_date': data.get('delivery_date', None),
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data.get('subtotal', 0.0),
        'tax_amount': data.get('tax_amount', 0.0),
        'total_amount': data['total_amount'],
        'status': _normalize_po_status(data['status']),
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'items': data.get('items', []),
        'converted_to_bill_id': data.get('converted_to_bill_id', None),
        'tenant_id': request.tenant_id,
        'lifecycle_status': 'ACTIVE',
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = purchase_orders_container.create_item(body=item)
        log_audit(
            "purchase_order", "create", item["id"], None, created_item,
            user_id=getattr(request, "user_id", None),
            tenant_id=request.tenant_id,
            entity_label=item.get("po_number"),
        )
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders', methods=['GET'])
@require_permission('purchase_orders', 'view')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by status'
        },
        {
            'name': 'vendor_id',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by vendor ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of purchase orders',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_purchase_orders():
    """Get all purchase orders with optional filters and optional metadata."""
    try:
        status_filter = (request.args.get('status') or '').strip()
        vendor_id_filter = (request.args.get('vendor_id') or '').strip()
        search_query = (request.args.get('search') or request.args.get('q') or '').strip()
        date_range = (request.args.get('date_range') or '').strip().lower()
        date_from = (request.args.get('date_from') or '').strip()
        date_to = (request.args.get('date_to') or '').strip()
        include_meta = str(request.args.get('include_meta', '')).lower() in ('1', 'true', 'yes')

        _ALLOWED_SORT_FIELDS = {
            'created_at': 'created_at',
            'po_number': 'po_number',
            'vendor_name': 'vendor_name',
            'order_date': 'order_date',
            'status': 'status',
            'total_amount': 'total_amount',
            'amount_paid': 'amount_paid',
            'balance_due': 'balance_due',
        }
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'created_at'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1

        limit_raw = request.args.get('limit', request.args.get('page_size', 10))
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit

        where = ["c.tenant_id = @tenant_id"]
        parameters = [{"name": "@tenant_id", "value": request.tenant_id}]

        lifecycle = (request.args.get('lifecycle') or 'active').strip().lower()
        if lifecycle == 'archived':
            where.append("UPPER(c.lifecycle_status) = @archived_status")
            parameters.append({"name": "@archived_status", "value": "ARCHIVED"})
        elif lifecycle != 'all':
            where.append("(NOT IS_DEFINED(c.lifecycle_status) OR UPPER(c.lifecycle_status) != @archived_status)")
            parameters.append({"name": "@archived_status", "value": "ARCHIVED"})

        if status_filter and status_filter.lower() != 'all' and lifecycle != 'archived':
            normalized_status = _normalize_po_status(status_filter)
            if normalized_status == 'Issued':
                where.append("(c.status = @status_issued OR c.status = @status_sent OR c.status = @status_confirmed)")
                parameters.extend([
                    {"name": "@status_issued", "value": "Issued"},
                    {"name": "@status_sent", "value": "Sent"},
                    {"name": "@status_confirmed", "value": "Confirmed"},
                ])
            else:
                where.append("c.status = @status")
                parameters.append({"name": "@status", "value": normalized_status})

        if vendor_id_filter:
            where.append("c.vendor_id = @vendor_id")
            parameters.append({"name": "@vendor_id", "value": vendor_id_filter})

        if search_query:
            where.append("(CONTAINS(LOWER(c.po_number), @q) OR CONTAINS(LOWER(c.subject), @q) OR CONTAINS(LOWER(c.vendor_name), @q) OR CONTAINS(LOWER(c.status), @q))")
            parameters.append({"name": "@q", "value": search_query.lower()})

        if date_range:
            today = datetime.utcnow().date()
            start_date = None
            end_date = None

            if date_range == 'this_week':
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif date_range == 'this_month':
                start_date = today.replace(day=1)
                if start_date.month == 12:
                    next_month = start_date.replace(year=start_date.year + 1, month=1, day=1)
                else:
                    next_month = start_date.replace(month=start_date.month + 1, day=1)
                end_date = next_month - timedelta(days=1)
            elif date_range == 'this_quarter':
                quarter_start_month = ((today.month - 1) // 3) * 3 + 1
                start_date = today.replace(month=quarter_start_month, day=1)
                if quarter_start_month == 10:
                    next_quarter = start_date.replace(year=start_date.year + 1, month=1, day=1)
                else:
                    next_quarter = start_date.replace(month=quarter_start_month + 3, day=1)
                end_date = next_quarter - timedelta(days=1)
            elif date_range == 'this_year':
                start_date = today.replace(month=1, day=1)
                end_date = today.replace(month=12, day=31)
            elif date_range == 'custom':
                if date_from:
                    start_date = datetime.fromisoformat(date_from).date()
                if date_to:
                    end_date = datetime.fromisoformat(date_to).date()

            if start_date:
                where.append("c.order_date >= @date_from")
                parameters.append({"name": "@date_from", "value": start_date.isoformat()})
            if end_date:
                where.append("c.order_date <= @date_to")
                parameters.append({"name": "@date_to", "value": end_date.isoformat()})

        where_sql = " AND ".join(where)
        base_query = f"SELECT * FROM c WHERE {where_sql}"
        sort_field = _ALLOWED_SORT_FIELDS[sort_by]

        legacy_mode = not include_meta and not any([
            request.args.get('page'),
            request.args.get('page_size'),
            request.args.get('limit'),
            search_query,
            date_range,
            date_from,
            date_to,
        ])

        if legacy_mode:
            query = f"{base_query} ORDER BY c.{sort_field} {sort_order}"
            items = list(purchase_orders_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True
            ))
            return jsonify([_compute_po_financials(item) for item in items]), 200

        query = f"{base_query} ORDER BY c.{sort_field} {sort_order} OFFSET {offset} LIMIT {limit}"

        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))

        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}"
        total_items = list(purchase_orders_container.query_items(
            query=count_query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        total = int(total_items[0]) if total_items else 0

        def _count_for_status(status_name):
            status_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql} AND c.status = @summary_status"
            status_rows = list(purchase_orders_container.query_items(
                query=status_query,
                parameters=[*parameters, {"name": "@summary_status", "value": status_name}],
                enable_cross_partition_query=True
            ))
            return int(status_rows[0]) if status_rows else 0

        summary = {
            'total': total,
            'draft': _count_for_status('Draft'),
            'issued': _count_for_status('Issued') + _count_for_status('Sent') + _count_for_status('Confirmed'),
            'received': _count_for_status('Received'),
            'cancelled': _count_for_status('Cancelled'),
        }

        return jsonify({
            'data': [_compute_po_financials(item) for item in items],
            'total': total,
            'page': page,
            'limit': limit,
            'summary': summary,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve purchase orders: {str(e)}"}), 500


@purchase_orders_blueprint.route('/purchase-orders/bulk', methods=['POST'])
@purchase_orders_blueprint.route('/purchase-orders/bulk-archive', methods=['POST'])
@require_permission('purchase_orders', 'edit')
def bulk_purchase_order_actions():
    """Perform bulk actions on purchase orders."""
    data = request.get_json() or {}
    action = data.get('action')
    ids = data.get('ids') or []

    if action not in ('delete', 'mark_received', 'cancel', 'mark_issued'):
        return jsonify({'error': 'Invalid bulk action'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty list'}), 400

    processed = []
    skipped = []
    now = datetime.utcnow().isoformat()

    for po_id in ids:
        try:
            rows = list(purchase_orders_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
                parameters=[
                    {"name": "@id", "value": po_id},
                    {"name": "@tenant_id", "value": request.tenant_id},
                ],
                enable_cross_partition_query=True
            ))

            if not rows:
                skipped.append({'id': po_id, 'reason': 'not_found'})
                continue

            po = rows[0]
            if action == 'delete':
                if _normalize_po_status(po.get('status')) == 'Billed':
                    skipped.append({'id': po_id, 'reason': 'billed'})
                    continue
                if _is_archived(po):
                    skipped.append({'id': po_id, 'reason': 'already_archived'})
                    continue
                archive_entity(
                    purchase_orders_container, po, 'purchase_order',
                    request.tenant_id, getattr(request, 'user_id', None), 'Bulk archive'
                )
                processed.append({'id': po_id, 'action': 'archive'})
                continue

            if action == 'mark_received':
                po['status'] = 'Received'
            elif action == 'cancel':
                po['status'] = 'Cancelled'
            elif action == 'mark_issued':
                po['status'] = 'Issued'

            po['updated_at'] = now
            purchase_orders_container.replace_item(item=po['id'], body=po)
            processed.append({'id': po_id, 'action': action})
        except Exception as inner_err:
            skipped.append({'id': po_id, 'reason': str(inner_err)})

    return jsonify({
        'processed': processed,
        'skipped': skipped,
        'success_count': len(processed),
        'failure_count': len(skipped),
    }), 200

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['GET'])
@require_permission('purchase_orders', 'view')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order retrieved successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def get_purchase_order(po_id):
    """Get a purchase order by ID"""
    try:
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": po_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        return jsonify(_compute_po_financials(items[0])), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['PUT'])
@require_permission('purchase_orders', 'edit')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'po_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'order_date': {'type': 'string', 'format': 'date'},
                    'delivery_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'status': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'items': {'type': 'array'}
                }
            },
            'description': 'Updated purchase order data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order updated successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def update_purchase_order(po_id):
    """Update a purchase order"""
    data = request.get_json()
    
    # Validate data
    errors = validate_po_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing purchase order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": po_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        po = items[0]
        if _is_archived(po):
            return jsonify({"error": "Purchase Order not found"}), 404

        before_snapshot = copy.deepcopy(po)

        # Update fields
        updatable_fields = [
            'po_number', 'vendor_id', 'vendor_name', 'order_date', 'delivery_date',
            'payment_terms', 'subtotal', 'tax_amount', 'total_amount', 'status',
            'notes', 'terms_conditions', 'items'
        ]
        
        for field in updatable_fields:
            if field in data:
                if field == 'status':
                    po[field] = _normalize_po_status(data[field])
                else:
                    po[field] = data[field]
        
        po['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = purchase_orders_container.replace_item(
            item=po['id'],
            body=po
        )
        log_audit(
            "purchase_order", "update", po_id, before_snapshot, updated_item,
            user_id=getattr(request, "user_id", None),
            tenant_id=request.tenant_id,
            entity_label=updated_item.get("po_number"),
        )
        return jsonify(_compute_po_financials(updated_item)), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update purchase order: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>', methods=['DELETE'])
@require_permission('purchase_orders', 'delete')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order deleted successfully'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def delete_purchase_order(po_id):
    """Archive a purchase order (soft delete)."""
    try:
        # Fetch the purchase order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": po_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))

        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404

        po = items[0]

        if _is_archived(po):
            return jsonify({"error": "Purchase Order already archived"}), 409

        # Check if already converted to bill
        if po.get('status') == 'Billed':
            return jsonify({"error": "Cannot archive a purchase order that has been billed"}), 400

        lifecycle_result = apply_lifecycle_action(
            container=purchase_orders_container,
            item=po,
            entity_type='purchase_order',
            tenant_id=request.tenant_id,
            user_id=getattr(request, 'user_id', None),
            requested_action='delete',
            reason=request.args.get('reason') or 'User requested delete',
        )

        return jsonify({
            "message": "Purchase Order archived successfully",
            "performedAction": lifecycle_result.get("performedAction"),
            "status": lifecycle_result.get("status"),
            "dependencySummary": lifecycle_result.get("dependencySummary", {}),
            "hardDeleteAllowed": lifecycle_result.get("hardDeleteAllowed", False),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Failed to archive purchase order: {str(e)}"}), 500


        return jsonify({"message": "Purchase Order archived successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to archive purchase order: {str(e)}"}), 500


@purchase_orders_blueprint.route('/purchase-orders/<po_id>/restore', methods=['POST'])
@require_permission('purchase_orders', 'edit')
def restore_purchase_order(po_id):
    """Restore an archived purchase order back to ACTIVE status."""
    items = list(purchase_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
        parameters=[
            {"name": "@id", "value": po_id},
            {"name": "@tenant_id", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({'error': 'Purchase Order not found'}), 404
    item = items[0]
    if not _is_archived(item):
        return jsonify({'error': 'Purchase Order is not archived'}), 422
    restored = restore_entity(
        purchase_orders_container, item, 'purchase_order', request.tenant_id,
        user_id=getattr(request, 'user_id', None), reason='User requested restore',
    )
    return jsonify({'message': 'Purchase Order restored', 'status': restored.get('status')}), 200


@purchase_orders_blueprint.route('/purchase-orders/<po_id>/dependencies', methods=['GET'])
@require_permission('purchase_orders', 'view')
def get_purchase_order_dependencies(po_id):
    """Check if a purchase order has dependent records before archiving."""
    result = check_entity_dependencies('purchase_order', po_id, request.tenant_id)
    return jsonify(result), 200

@purchase_orders_blueprint.route('/purchase-orders/<po_id>/convert-bill', methods=['POST'])
@require_permission('bills', 'create')
@swag_from({
    'tags': ['Purchase Orders'],
    'parameters': [
        {
            'name': 'po_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Purchase Order ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'}
                },
                'required': ['bill_number']
            },
            'description': 'Bill number for the new bill'
        }
    ],
    'responses': {
        '200': {
            'description': 'Purchase Order converted to bill successfully',
            'examples': {
                'application/json': {
                    'message': 'Purchase Order converted to bill successfully',
                    'bill_id': 'uuid',
                    'bill_number': 'BILL-001'
                }
            }
        },
        '400': {
            'description': 'Purchase Order already billed or validation error'
        },
        '404': {
            'description': 'Purchase Order not found'
        }
    }
})
def convert_po_to_bill(po_id):
    """Convert a purchase order to a bill"""
    data = request.get_json() or {}
    bill_number = (data.get('bill_number') or '').strip()
    
    try:
        # Fetch the purchase order
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": po_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Purchase Order not found"}), 404
        
        po = items[0]
        if _is_archived(po):
            return jsonify({"error": "Archived purchase orders cannot be converted"}), 409
        
        # Check if already billed
        if po.get('status') == 'Billed' or po.get('converted_to_bill_id'):
            return jsonify({"error": "Purchase Order has already been billed"}), 400

        before_convert = copy.deepcopy(po)

        if not bill_number:
            bill_number = f"BILL-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"

        # Create bill from purchase order
        now = datetime.utcnow().isoformat()
        bill = {
            'id': str(uuid.uuid4()),
            'bill_number': bill_number,
            'vendor_id': po['vendor_id'],
            'vendor_name': po.get('vendor_name', ''),
            'bill_date': datetime.utcnow().date().isoformat(),
            'due_date': po.get('delivery_date', datetime.utcnow().date().isoformat()),
            'payment_terms': po.get('payment_terms', ''),
            'subtotal': po.get('subtotal', 0.0),
            'tax_amount': po.get('tax_amount', 0.0),
            'total_amount': po['total_amount'],
            'amount_paid': 0.0,
            'balance_due': po['total_amount'],
            'payment_status': 'Unpaid',
            'notes': po.get('notes', ''),
            'terms_conditions': po.get('terms_conditions', ''),
            'items': po.get('items', []),
            'expenses': [],
            'converted_from_po_id': po_id,
            'tenant_id': request.tenant_id,
            'created_at': now,
            'updated_at': now
        }
        
        created_bill = bills_container.create_item(body=bill)
        
        # Update purchase order status
        po['status'] = 'Billed'
        po['converted_to_bill_id'] = created_bill['id']
        po['updated_at'] = now
        
        purchase_orders_container.replace_item(
            item=po['id'],
            body=po
        )
        log_audit_event({
            "action": "CONVERTED",
            "entity": "purchase_order",
            "entity_id": po_id,
            "entity_label": po.get("po_number"),
            "before": before_convert,
            "after": po,
            "metadata": {
                "workflow": "purchase_order_to_bill",
                "target_entity": "bill",
                "target_entity_id": created_bill["id"],
                "target_entity_label": created_bill.get("bill_number"),
            },
            "user_id": getattr(request, "user_id", None),
            "tenant_id": request.tenant_id,
        })

        return jsonify({
            "message": "Purchase Order converted to bill successfully",
            "bill_id": created_bill['id'],
            "bill_number": created_bill['bill_number']
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to convert purchase order to bill: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/next-number', methods=['GET'])
@require_permission('purchase_orders', 'view')
@swag_from({
    'tags': ['Purchase Orders'],
    'responses': {
        '200': {
            'description': 'Next available PO number',
            'examples': {
                'application/json': {
                    'next_number': 'PO-001'
                }
            }
        }
    }
})
def get_next_po_number():
    """Get the next available purchase order number"""
    try:
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(purchase_orders_container.query_items(
            query=query,
            parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "PO-001"}), 200
        
        last_po = items[0]
        last_number = last_po.get('po_number', 'PO-000')
        
        # Extract number part (assuming format PO-XXX)
        try:
            prefix, num_str = last_number.rsplit('-', 1)
            next_num = int(num_str) + 1
            next_number = f"{prefix}-{next_num:03d}"
        except:
            next_number = "PO-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate next PO number: {str(e)}"}), 500

@purchase_orders_blueprint.route('/purchase-orders/<po_id>/pdf', methods=['GET'])
@require_permission('purchase_orders', 'view')
def get_po_pdf(po_id):
    """Generate and return a PDF for a purchase order."""
    items = list(purchase_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": po_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Purchase order not found'}), 404
    po = items[0]
    doc = {
        **po,
        'invoice_number': po.get('po_number', po['id']),
        'customer_name': po.get('vendor_name', po.get('vendor_id', 'Vendor')),
        'items': [
            {**item, 'name': item.get('item_name', item.get('name', ''))}
            for item in po.get('items', [])
        ]
    }
    try:
        branding = branding_for_document(po, request.tenant_id)
        pdf_bytes = build_invoice_pdf(doc, branding=branding, doc_type='purchase_order')
        ref = po.get('po_number', 'po').replace('/', '-')
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename={ref}.pdf'
        return response
    except Exception as e:
        return jsonify({'error': f'Failed to generate PDF: {str(e)}'}), 500


@purchase_orders_blueprint.route('/purchase-orders/<po_id>/send-email', methods=['POST'])
@require_permission('purchase_orders', 'edit')
def send_po_email(po_id):
    """Send a purchase order to the vendor via Azure Communication Services."""
    import os
    from azure.communication.email import EmailClient

    connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
    sender_address    = os.getenv('SENDER_EMAIL', 'noreply@solidevelectrosoft.com')
    if not connection_string:
        return jsonify({'error': 'Email service not configured on the server'}), 503

    data = request.get_json() or {}
    attach_pdf = bool(data.get('attach_pdf', False))

    items = list(purchase_orders_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": po_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if not items:
        return jsonify({'error': 'Purchase order not found'}), 404
    po = items[0]
    if _is_archived(po):
        return jsonify({'error': 'Archived purchase orders cannot be emailed'}), 409

    recipient_email = data.get('recipient_email') or po.get('vendor_email', '').strip()
    if not recipient_email:
        return jsonify({'error': 'No recipient email found on this purchase order'}), 400

    po_number     = po.get('po_number', po['id'])
    vendor_name   = po.get('vendor_name', 'Vendor')
    order_date    = po.get('order_date', '')
    delivery_date = po.get('delivery_date', po.get('expected_delivery', ''))
    total_amount  = float(po.get('total_amount', 0))
    personal_msg  = data.get('message', '')

    _branding = _get_tenant_branding(request.tenant_id)

    from smart_invoice_pro.services.email_template_service import render_branded_email
    html_content, _plain_content = render_branded_email(
        doc_type='purchase_order',
        context={
            'doc_number':    po_number,
            'customer_name': vendor_name,
            'issue_date':    order_date,
            'due_date':      delivery_date,
            'total_amount':  total_amount,
            'balance_due':   total_amount,
            'subtotal':      float(po.get('subtotal', 0)),
            'total_tax':     float(po.get('total_tax', 0)),
            'items':         [{**i, 'name': i.get('item_name', i.get('name', ''))} for i in po.get('items', [])],
            'message':       personal_msg,
        },
        branding=_branding,
    )

    email_message = {
        "senderAddress": sender_address,
        "recipients": {"to": [{"address": recipient_email}]},
        "content": {
            "subject": f"Purchase Order {po_number}",
            "html": html_content
        }
    }

    if attach_pdf:
        try:
            doc = {
                **po,
                'invoice_number': po_number,
                'customer_name': vendor_name,
                'items': [{**i, 'name': i.get('item_name', i.get('name', ''))} for i in po.get('items', [])]
            }
            pdf_bytes = build_invoice_pdf(doc, branding=_branding, doc_type='purchase_order')
            email_message["attachments"] = [{
                "name": f"po_{po_number}.pdf",
                "contentType": "application/pdf",
                "contentInBase64": base64.b64encode(pdf_bytes).decode('utf-8')
            }]
        except Exception as pdf_err:
            print(f"WARNING: PO PDF generation failed: {pdf_err}")

    try:
        client = EmailClient.from_connection_string(connection_string)
        poller = client.begin_send(email_message)
        result = poller.result()

        now = datetime.utcnow().isoformat()
        po['email_status']  = 'sent'
        po['email_sent_at'] = now
        po['updated_at']    = now
        if not po.get('brand_snapshot'):
            po['brand_snapshot'] = {
                'primary_color':     _branding.get('primary_color', ''),
                'accent_color':      _branding.get('accent_color', ''),
                'secondary_color':   _branding.get('secondary_color', ''),
                'logo_url':          _branding.get('logo_url', ''),
                'organization_name': _branding.get('organization_name', ''),
                'snapshotted_at':    now,
            }
        purchase_orders_container.replace_item(item=po['id'], body=po)

        return jsonify({
            'message':    'Purchase order email sent successfully',
            'sent_to':    recipient_email,
            'message_id': result.get('id'),
        }), 200
    except Exception as e:
        return jsonify({'error': f'Failed to send email: {str(e)}'}), 500