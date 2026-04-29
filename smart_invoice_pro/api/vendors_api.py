from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import vendors_container
from smart_invoice_pro.utils.validation_utils import (
    make_error_response, collect_errors,
    validate_required, validate_email, validate_gst, validate_mobile,
    VALIDATION_ERROR, NOT_FOUND_ERROR, SERVER_ERROR,
)
import uuid
from flasgger import swag_from
from datetime import datetime
from smart_invoice_pro.utils.audit_logger import log_audit_event

vendors_blueprint = Blueprint('vendors', __name__)


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
        'status': data.get('status', 'Active'),
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
        search_term = request.args.get('search', '')

        _ALLOWED_SORT_FIELDS = {'created_at', 'vendor_name', 'name'}
        sort_by = request.args.get('sort_by', 'vendor_name')
        sort_order = request.args.get('sort_order', 'asc').upper()
        if sort_by not in _ALLOWED_SORT_FIELDS:
            sort_by = 'vendor_name'
        if sort_order not in ('ASC', 'DESC'):
            sort_order = 'ASC'

        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        params = [{"name": "@tenant_id", "value": request.tenant_id}]
        if search_term:
            query += " AND CONTAINS(LOWER(c.name), @search)"
            params.append({"name": "@search", "value": search_term.lower()})

        query += f" ORDER BY c.{sort_by} {sort_order}"
        
        items = list(vendors_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to retrieve vendors: {str(e)}"}), 500

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
        before_snapshot = dict(vendor)
        
        vendors_container.delete_item(
            item=vendor['id'],
            partition_key=vendor['vendor_id']
        )

        log_audit_event({
            "action": "DELETE",
            "entity": "vendor",
            "entity_id": vendor_id,
            "before": before_snapshot,
            "after": None,
            "metadata": {"event": "delete_vendor"},
            "tenant_id": request.tenant_id,
            "user_id": getattr(request, "user_id", None),
        })
        
        return jsonify({"message": "Vendor deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete vendor: {str(e)}"}), 500
