from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import bills_container, stock_container
from smart_invoice_pro.utils.archive_service import archive_entity, restore_entity
from smart_invoice_pro.utils.bulk_archive_contracts import (
    add_archive_failure,
    add_archive_success,
    finalize_bulk_archive_result,
    init_bulk_archive_result,
)
from smart_invoice_pro.utils.dependency_checker import check_entity_dependencies
from smart_invoice_pro.utils.domain_events import record_bulk_archive_completed
from smart_invoice_pro.utils.audit_logger import log_bulk_archive_summary
import uuid
from flasgger import swag_from
from datetime import date, datetime, timedelta
from enum import Enum

bills_blueprint = Blueprint('bills', __name__)


def _is_archived(item):
    return str(item.get('lifecycle_status') or item.get('status') or '').upper() == 'ARCHIVED'

class PaymentStatus(Enum):
    Draft = 'Draft'
    Open = 'Open'
    Unpaid = 'Unpaid'
    PartiallyPaid = 'Partially Paid'
    Paid = 'Paid'
    Overdue = 'Overdue'


SYSTEM_FIELDS = {'_rid', '_self', '_etag', '_attachments', '_ts'}


def _to_float(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _parse_date(value):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if len(raw) >= 10:
            return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None
    return None


def _normalize_payment_status(raw_status):
    normalized = str(raw_status or '').strip()
    if normalized in PaymentStatus._value2member_map_:
        return normalized
    if normalized.lower() == 'partiallypaid':
        return 'Partially Paid'
    return normalized


def _derive_bill_bucket(item):
    status = _normalize_payment_status(item.get('payment_status'))
    total_amount = _to_float(item.get('total_amount', 0.0), 0.0)
    amount_paid = _to_float(item.get('amount_paid', 0.0), 0.0)
    balance_due = item.get('balance_due', None)
    if balance_due is None:
        balance_due = max(total_amount - amount_paid, 0.0)
    else:
        balance_due = _to_float(balance_due, 0.0)

    due_date = _parse_date(item.get('due_date'))
    is_overdue = balance_due > 0 and due_date is not None and due_date < date.today()

    if status == 'Draft':
        bucket = 'Draft'
    elif status == 'Paid' or balance_due <= 0:
        bucket = 'Paid'
    elif status == 'Overdue' or is_overdue:
        bucket = 'Overdue'
    else:
        bucket = 'Open'

    return {
        **item,
        'payment_status': status or item.get('payment_status', ''),
        'amount_paid': amount_paid,
        'balance_due': balance_due,
        'status_bucket': bucket,
        'is_overdue': bool(bucket == 'Overdue'),
    }


def _sanitize_bill(item):
    if not isinstance(item, dict):
        return item
    return {
        key: value
        for key, value in item.items()
        if key not in SYSTEM_FIELDS
    }


def _is_truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'y')


def _date_bounds(date_range, date_from, date_to):
    today = date.today()
    start_date = None
    end_date = None

    if date_range == 'this_week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif date_range == 'this_month':
        start_date = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
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
            start_date = _parse_date(date_from)
        if date_to:
            end_date = _parse_date(date_to)

    return start_date, end_date

def validate_bill_data(data, is_update=False):
    """Validate bill data"""
    errors = {}
    
    if not is_update:
        required_fields = ['bill_number', 'vendor_id', 'bill_date', 'due_date', 'total_amount']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate payment status
    if 'payment_status' in data and data['payment_status'] not in PaymentStatus._value2member_map_:
        errors['payment_status'] = f'Invalid payment status: {data["payment_status"]}'
    
    # Validate dates
    if 'bill_date' in data and 'due_date' in data:
        try:
            bill = datetime.fromisoformat(data['bill_date'].replace('Z', '+00:00'))
            due = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
            if due < bill:
                errors['due_date'] = 'Due date cannot be before bill date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    return errors

@bills_blueprint.route('/bills', methods=['POST'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'bill_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'payment_status': {'type': 'string', 'enum': ['Unpaid', 'Partially Paid', 'Paid', 'Overdue']},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'items': {'type': 'array'},
                    'expenses': {'type': 'array'},
                    'converted_from_po_id': {'type': 'string'}
                },
                'required': ['bill_number', 'vendor_id', 'bill_date', 'due_date', 'total_amount']
            },
            'description': 'Bill data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Bill created successfully',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'bill_number': 'BILL-001',
                    'vendor_id': '123',
                    'payment_status': 'Unpaid'
                }
            }
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_bill():
    """Create a new bill"""
    data = request.get_json()
    
    # Validate data
    errors = validate_bill_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    normalized_status = _normalize_payment_status(data.get('payment_status', 'Unpaid')) or 'Unpaid'

    item = {
        'id': str(uuid.uuid4()),
        'bill_number': data['bill_number'],
        'vendor_id': data['vendor_id'],
        'vendor_name': data.get('vendor_name', ''),
        'bill_date': data['bill_date'],
        'due_date': data['due_date'],
        'payment_terms': data.get('payment_terms', ''),
        'subtotal': data.get('subtotal', 0.0),
        'tax_amount': data.get('tax_amount', 0.0),
        'total_amount': data['total_amount'],
        'amount_paid': data.get('amount_paid', 0.0),
        'balance_due': data.get('balance_due', data['total_amount']),
        'payment_status': normalized_status,
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'items': data.get('items', []),
        'expenses': data.get('expenses', []),
        'converted_from_po_id': data.get('converted_from_po_id', None),
        'reference': data.get('reference', ''),
        'tenant_id': request.tenant_id,
        'payment_history': [],  # Track payment records
        'lifecycle_status': 'ACTIVE',
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = bills_container.create_item(body=item)
        
        # Increment stock for each item in the bill
        for bill_item in data.get('items', []):
            if 'product_id' in bill_item and 'quantity' in bill_item:
                try:
                    stock_transaction = {
                        'id': str(uuid.uuid4()),
                        'product_id': str(bill_item['product_id']),
                        'quantity': float(bill_item['quantity']),
                        'type': 'IN',
                        'source': f'Bill {data["bill_number"]}',
                        'reference_id': item['id'],
                        'timestamp': now
                    }
                    stock_container.create_item(body=stock_transaction)
                except Exception as e:
                    print(f"Error updating stock for product {bill_item.get('product_id')}: {str(e)}")

        return jsonify(_sanitize_bill(_derive_bill_bucket(created_item))), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create bill: {str(e)}"}), 500

@bills_blueprint.route('/bills', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'payment_status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by payment status'
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
            'description': 'List of bills',
            'schema': {
                'type': 'array',
                'items': {
                    'type': 'object'
                }
            }
        }
    }
})
def get_bills():
    """Get bills with optional filtering, sorting, pagination and metadata."""
    try:
        status_filter = (request.args.get('status') or request.args.get('payment_status') or '').strip()
        vendor_id_filter = (request.args.get('vendor_id') or '').strip()
        search_query = (request.args.get('search') or request.args.get('q') or '').strip()
        date_range = (request.args.get('range') or request.args.get('date_range') or 'all').strip().lower()
        date_from = (request.args.get('date_from') or '').strip()
        date_to = (request.args.get('date_to') or '').strip()
        include_meta = _is_truthy(request.args.get('include_meta'))

        min_amount_raw = (request.args.get('min_amount') or '').strip()
        max_amount_raw = (request.args.get('max_amount') or '').strip()

        sort_map = {
            'created_at': 'created_at',
            'bill_date': 'bill_date',
            'bill_number': 'bill_number',
            'vendor_name': 'vendor_name',
            'due_date': 'due_date',
            'total_amount': 'total_amount',
            'amount': 'total_amount',
            'status': 'payment_status',
            'payment_status': 'payment_status',
        }
        sort_by = (request.args.get('sort_by') or 'created_at').strip()
        sort_order = (request.args.get('sort_order') or 'desc').strip().upper()
        if sort_by not in sort_map:
            sort_by = 'created_at'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'DESC'

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1

        page_size_raw = request.args.get('page_size', request.args.get('limit', 10))
        try:
            page_size = int(page_size_raw)
        except (TypeError, ValueError):
            page_size = 10
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        lifecycle = (request.args.get('lifecycle') or 'active').strip().lower()

        where = ["c.tenant_id = @tenant_id"]
        parameters = [{"name": "@tenant_id", "value": request.tenant_id}]

        if lifecycle == 'archived':
            where.append("UPPER(c.lifecycle_status) = @archived_status")
            parameters.append({"name": "@archived_status", "value": "ARCHIVED"})
        elif lifecycle != 'all':
            where.append("(NOT IS_DEFINED(c.lifecycle_status) OR UPPER(c.lifecycle_status) != @archived_status)")
            parameters.append({"name": "@archived_status", "value": "ARCHIVED"})

        status_key = status_filter.lower()
        if status_key and status_key != 'all' and lifecycle != 'archived':
            if status_key == 'open':
                where.append("(c.payment_status = @open_status OR c.payment_status = @unpaid_status OR c.payment_status = @partial_status)")
                parameters.extend([
                    {"name": "@open_status", "value": "Open"},
                    {"name": "@unpaid_status", "value": "Unpaid"},
                    {"name": "@partial_status", "value": "Partially Paid"},
                ])
            elif status_key == 'paid':
                where.append("c.payment_status = @paid_status")
                parameters.append({"name": "@paid_status", "value": "Paid"})
            elif status_key == 'draft':
                where.append("c.payment_status = @draft_status")
                parameters.append({"name": "@draft_status", "value": "Draft"})
            elif status_key == 'overdue':
                today_iso = date.today().isoformat()
                where.append("(c.payment_status = @overdue_status OR (IS_DEFINED(c.due_date) AND c.due_date < @today_iso AND c.payment_status != @paid_status))")
                parameters.extend([
                    {"name": "@overdue_status", "value": "Overdue"},
                    {"name": "@today_iso", "value": today_iso},
                    {"name": "@paid_status", "value": "Paid"},
                ])
            else:
                where.append("c.payment_status = @status")
                parameters.append({"name": "@status", "value": status_filter})

        if vendor_id_filter:
            where.append("c.vendor_id = @vendor_id")
            parameters.append({"name": "@vendor_id", "value": vendor_id_filter})

        if search_query:
            where.append("(CONTAINS(LOWER(c.bill_number), @q) OR CONTAINS(LOWER(c.vendor_name), @q) OR CONTAINS(LOWER(c.reference), @q) OR CONTAINS(LOWER(c.subject), @q))")
            parameters.append({"name": "@q", "value": search_query.lower()})

        date_start, date_end = _date_bounds(date_range, date_from, date_to)
        if date_start:
            where.append("IS_DEFINED(c.bill_date) AND c.bill_date >= @date_start")
            parameters.append({"name": "@date_start", "value": date_start.isoformat()})
        if date_end:
            where.append("IS_DEFINED(c.bill_date) AND c.bill_date <= @date_end")
            parameters.append({"name": "@date_end", "value": date_end.isoformat()})

        if min_amount_raw:
            where.append("c.total_amount >= @min_amount")
            parameters.append({"name": "@min_amount", "value": _to_float(min_amount_raw, 0.0)})
        if max_amount_raw:
            where.append("c.total_amount <= @max_amount")
            parameters.append({"name": "@max_amount", "value": _to_float(max_amount_raw, 0.0)})

        where_sql = " AND ".join(where)
        base_query = f"SELECT * FROM c WHERE {where_sql}"

        legacy_mode = not include_meta and not any([
            request.args.get('page'),
            request.args.get('page_size'),
            request.args.get('limit'),
            search_query,
            vendor_id_filter,
            date_range not in ('', 'all'),
            min_amount_raw,
            max_amount_raw,
            request.args.get('status'),
            request.args.get('q'),
        ])

        sort_field = sort_map[sort_by]
        order_sql = f" ORDER BY c.{sort_field} {sort_order}"

        if legacy_mode:
            legacy_items = list(bills_container.query_items(
                query=f"{base_query}{order_sql}",
                parameters=parameters,
                enable_cross_partition_query=True,
            ))
            normalized = [_sanitize_bill(_derive_bill_bucket(item)) for item in legacy_items]
            return jsonify(normalized), 200

        paginated_items = list(bills_container.query_items(
            query=f"{base_query}{order_sql} OFFSET {offset} LIMIT {page_size}",
            parameters=parameters,
            enable_cross_partition_query=True,
        ))

        count_rows = list(bills_container.query_items(
            query=f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql}",
            parameters=parameters,
            enable_cross_partition_query=True,
        ))
        total = int(count_rows[0]) if count_rows else 0

        summary = {
            'total': total,
            'draft': 0,
            'open': 0,
            'paid': 0,
            'overdue': 0,
        }

        for key, clause, extra_params in [
            ('draft', "c.payment_status = @summary_draft", [{"name": "@summary_draft", "value": "Draft"}]),
            ('open', "(c.payment_status = @summary_open OR c.payment_status = @summary_unpaid OR c.payment_status = @summary_partial)", [
                {"name": "@summary_open", "value": "Open"},
                {"name": "@summary_unpaid", "value": "Unpaid"},
                {"name": "@summary_partial", "value": "Partially Paid"},
            ]),
            ('paid', "c.payment_status = @summary_paid", [{"name": "@summary_paid", "value": "Paid"}]),
            ('overdue', "(c.payment_status = @summary_overdue OR (IS_DEFINED(c.due_date) AND c.due_date < @summary_today AND c.payment_status != @summary_paid))", [
                {"name": "@summary_overdue", "value": "Overdue"},
                {"name": "@summary_today", "value": date.today().isoformat()},
                {"name": "@summary_paid", "value": "Paid"},
            ]),
        ]:
            rows = list(bills_container.query_items(
                query=f"SELECT VALUE COUNT(1) FROM c WHERE {where_sql} AND {clause}",
                parameters=[*parameters, *extra_params],
                enable_cross_partition_query=True,
            ))
            summary[key] = int(rows[0]) if rows else 0

        payload = {
            'data': [_sanitize_bill(_derive_bill_bucket(item)) for item in paginated_items],
            'total': total,
            'page': page,
            'page_size': page_size,
            'limit': page_size,
            'sort_by': sort_by,
            'sort_order': sort_order.lower(),
            'summary': summary,
        }
        return jsonify(payload), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve bills: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill retrieved successfully'
        },
        '404': {
            'description': 'Bill not found'
        }
    }
})
def get_bill(bill_id):
    """Get a bill by ID"""
    try:
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(bills_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": bill_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404

        if _is_archived(items[0]):
            return jsonify({"error": "Bill not found"}), 404
        
        return jsonify(_sanitize_bill(_derive_bill_bucket(items[0]))), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve bill: {str(e)}"}), 500


@bills_blueprint.route('/bills/<bill_id>/dependencies', methods=['GET'])
def get_bill_dependencies(bill_id):
    """Check if a bill has dependent records before archiving."""
    result = check_entity_dependencies('bill', bill_id, request.tenant_id)
    return jsonify(result), 200

@bills_blueprint.route('/bills/<bill_id>', methods=['PUT'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'bill_number': {'type': 'string'},
                    'vendor_id': {'type': 'string'},
                    'vendor_name': {'type': 'string'},
                    'bill_date': {'type': 'string', 'format': 'date'},
                    'due_date': {'type': 'string', 'format': 'date'},
                    'payment_terms': {'type': 'string'},
                    'subtotal': {'type': 'number'},
                    'tax_amount': {'type': 'number'},
                    'total_amount': {'type': 'number'},
                    'amount_paid': {'type': 'number'},
                    'balance_due': {'type': 'number'},
                    'payment_status': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'items': {'type': 'array'},
                    'expenses': {'type': 'array'}
                }
            },
            'description': 'Updated bill data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill updated successfully'
        },
        '404': {
            'description': 'Bill not found'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def update_bill(bill_id):
    """Update a bill"""
    data = request.get_json()
    
    # Validate data
    errors = validate_bill_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing bill
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(bills_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": bill_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        bill = items[0]

        if _is_archived(bill):
            return jsonify({"error": "Bill not found"}), 404
        
        # Update fields
        updatable_fields = [
            'bill_number', 'vendor_id', 'vendor_name', 'bill_date', 'due_date',
            'payment_terms', 'subtotal', 'tax_amount', 'total_amount', 'amount_paid',
            'balance_due', 'payment_status', 'notes', 'terms_conditions', 'items', 'expenses', 'reference'
        ]
        
        for field in updatable_fields:
            if field in data:
                bill[field] = data[field]
        
        bill['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = bills_container.replace_item(
            item=bill['id'],
            body=bill
        )

        return jsonify(_sanitize_bill(_derive_bill_bucket(updated_item))), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update bill: {str(e)}"}), 500

@bills_blueprint.route('/bills/<bill_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Bill deleted successfully'
        },
        '404': {
            'description': 'Bill not found'
        }
    }
})
def delete_bill(bill_id):
    """Archive a bill (soft delete)."""
    try:
        # Fetch the bill
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(bills_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": bill_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))

        if not items:
            return jsonify({"error": "Bill not found"}), 404

        bill = items[0]

        if _is_archived(bill):
            return jsonify({"error": "Bill already archived"}), 409

        # Check if bill has been paid
        if bill.get('payment_status') == 'Paid':
            return jsonify({"error": "Cannot archive a bill that has been paid"}), 400

        reason = request.args.get('reason') or 'Archived by user'
        archive_entity(
            bills_container, bill, 'bill',
            request.tenant_id, getattr(request, 'user_id', None), reason
        )

        return jsonify({"message": "Bill archived successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to archive bill: {str(e)}"}), 500


        return jsonify({"message": "Bill archived successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to archive bill: {str(e)}"}), 500


@bills_blueprint.route('/bills/<bill_id>/restore', methods=['POST'])
def restore_bill(bill_id):
    """Restore an archived bill back to ACTIVE status."""
    items = list(bills_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
        parameters=[
            {"name": "@id", "value": bill_id},
            {"name": "@tenant_id", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({'error': 'Bill not found'}), 404
    item = items[0]
    if not _is_archived(item):
        return jsonify({'error': 'Bill is not archived'}), 422
    restored = restore_entity(
        bills_container, item, 'bill', request.tenant_id,
        user_id=getattr(request, 'user_id', None), reason='User requested restore',
    )
    return jsonify({'message': 'Bill restored', 'status': restored.get('status')}), 200


@bills_blueprint.route('/bills/bulk-archive', methods=['POST'])
@bills_blueprint.route('/bills/bulk', methods=['POST'])
def bulk_archive_bills():
    """Lifecycle-aware bulk archive for bills."""
    payload = request.get_json() or {}
    ids = payload.get('ids') or []
    action = str(payload.get('action') or 'archive').strip().lower()

    if action not in {'archive', 'delete'}:
        return jsonify({'error': 'Invalid bulk action'}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'ids must be a non-empty array'}), 400

    result = init_bulk_archive_result('bill', ids)
    tenant_id = request.tenant_id
    user_id = getattr(request, 'user_id', None)

    for bill_id in ids:
        try:
            rows = list(bills_container.query_items(
                query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id",
                parameters=[
                    {"name": "@id", "value": bill_id},
                    {"name": "@tenant_id", "value": tenant_id},
                ],
                enable_cross_partition_query=True,
            ))

            if not rows:
                add_archive_failure(result, bill_id, 'NOT_FOUND', 'Bill not found')
                continue

            bill = rows[0]
            deps = check_entity_dependencies('bill', bill_id, tenant_id)

            if _is_archived(bill):
                add_archive_failure(
                    result,
                    bill_id,
                    'ALREADY_ARCHIVED',
                    'Bill already archived',
                    dependency_summary=deps.get('dependencySummary', {}),
                )
                continue

            if str(bill.get('payment_status') or '').strip().lower() == 'paid':
                add_archive_failure(
                    result,
                    bill_id,
                    'LOCKED_BY_WORKFLOW',
                    'Cannot archive a bill that has been paid',
                    dependency_summary=deps.get('dependencySummary', {}),
                )
                continue

            archive_entity(
                bills_container,
                bill,
                'bill',
                tenant_id,
                user_id,
                reason='bulk_archive',
            )
            add_archive_success(
                result,
                bill_id,
                dependency_summary=deps.get('dependencySummary', {}),
                metadata={'message': 'Bill archived successfully'},
            )
        except Exception as exc:
            add_archive_failure(result, bill_id, 'INTERNAL_ERROR', str(exc))

    finalize_bulk_archive_result(result)
    log_bulk_archive_summary(
        tenant_id=tenant_id,
        user_id=user_id,
        entity_type='bill',
        requested_count=result['requestedCount'],
        success_count=result['successCount'],
        failed_count=result['failedCount'],
        dependency_summary=result.get('dependencySummary', {}),
    )
    record_bulk_archive_completed(tenant_id, user_id, 'bill', result)
    return jsonify(result), 200

@bills_blueprint.route('/bills/<bill_id>/record-payment', methods=['POST'])
@swag_from({
    'tags': ['Bills'],
    'parameters': [
        {
            'name': 'bill_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Bill ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'amount': {'type': 'number'},
                    'payment_date': {'type': 'string', 'format': 'date'},
                    'payment_method': {'type': 'string'},
                    'reference': {'type': 'string'},
                    'notes': {'type': 'string'}
                },
                'required': ['amount', 'payment_date']
            },
            'description': 'Payment record'
        }
    ],
    'responses': {
        '200': {
            'description': 'Payment recorded successfully',
            'examples': {
                'application/json': {
                    'message': 'Payment recorded successfully',
                    'payment_status': 'Paid',
                    'balance_due': 0
                }
            }
        },
        '400': {
            'description': 'Invalid payment amount or bill not found'
        }
    }
})
def record_payment(bill_id):
    """Record a payment against a bill"""
    data = request.get_json()
    amount = data.get('amount', 0)
    
    if amount <= 0:
        return jsonify({"error": "Payment amount must be greater than zero"}), 400
    
    try:
        # Fetch the bill
        query = "SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tenant_id"
        items = list(bills_container.query_items(
            query=query,
            parameters=[
                {"name": "@id", "value": bill_id},
                {"name": "@tenant_id", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Bill not found"}), 404
        
        bill = items[0]
        if _is_archived(bill):
            return jsonify({"error": "Archived bills cannot receive payments"}), 409
        
        # Validate payment amount
        if amount > bill['balance_due']:
            return jsonify({"error": "Payment amount exceeds balance due"}), 400
        
        # Record payment
        payment_record = {
            'id': str(uuid.uuid4()),
            'amount': amount,
            'payment_date': data.get('payment_date'),
            'payment_method': data.get('payment_method', ''),
            'reference': data.get('reference', ''),
            'notes': data.get('notes', ''),
            'recorded_at': datetime.utcnow().isoformat()
        }
        
        if 'payment_history' not in bill:
            bill['payment_history'] = []
        
        bill['payment_history'].append(payment_record)
        
        # Update payment amounts
        bill['amount_paid'] = bill.get('amount_paid', 0) + amount
        bill['balance_due'] = bill['total_amount'] - bill['amount_paid']
        
        # Update payment status
        if bill['balance_due'] <= 0:
            bill['payment_status'] = 'Paid'
        elif bill['amount_paid'] > 0:
            bill['payment_status'] = 'Partially Paid'
        else:
            bill['payment_status'] = 'Unpaid'
        
        bill['updated_at'] = datetime.utcnow().isoformat()
        
        updated_bill = bills_container.replace_item(
            item=bill['id'],
            body=bill
        )
        
        return jsonify({
            "message": "Payment recorded successfully",
            "payment_status": updated_bill['payment_status'],
            "amount": amount,
            "balance_due": updated_bill['balance_due']
        }), 200
    
    except Exception as e:
        return jsonify({"error": f"Failed to record payment: {str(e)}"}), 500

@bills_blueprint.route('/bills/next-number', methods=['GET'])
@swag_from({
    'tags': ['Bills'],
    'responses': {
        '200': {
            'description': 'Next available bill number',
            'examples': {
                'application/json': {
                    'next_number': 'BILL-001'
                }
            }
        }
    }
})
def get_next_bill_number():
    """Get the next available bill number"""
    try:
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
        items = list(bills_container.query_items(
            query=query,
            parameters=[{"name": "@tenant_id", "value": request.tenant_id}],
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"next_number": "BILL-001"}), 200
        
        last_bill = items[0]
        last_number = last_bill.get('bill_number', 'BILL-000')
        
        # Extract number part (assuming format BILL-XXX)
        try:
            prefix, num_str = last_number.rsplit('-', 1)
            next_num = int(num_str) + 1
            next_number = f"{prefix}-{next_num:03d}"
        except:
            next_number = "BILL-001"
        
        return jsonify({"next_number": next_number}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to generate next bill number: {str(e)}"}), 500
