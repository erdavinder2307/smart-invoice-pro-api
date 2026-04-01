from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import vendors_container
import uuid
from flasgger import swag_from
from datetime import datetime
from smart_invoice_pro.utils.audit_logger import log_audit_event

vendors_blueprint = Blueprint('vendors', __name__)

def validate_vendor_data(data, is_update=False):
    """Validate vendor data"""
    errors = {}
    
    if not is_update:
        required_fields = ['name', 'contact_person']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate email format if provided
    if 'email' in data and data['email']:
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, data['email']):
            errors['email'] = 'Invalid email format'
    
    return errors

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
    data = request.get_json()
    
    # Validate data
    errors = validate_vendor_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    vendor_id = str(uuid.uuid4())
    
    item = {
        'id': vendor_id,
        'vendor_id': vendor_id,  # For partition key
        'name': data['name'],
        'contact_person': data['contact_person'],
        'email': data.get('email', ''),
        'phone': data.get('phone', ''),
        'address': data.get('address', ''),
        'city': data.get('city', ''),
        'state': data.get('state', ''),
        'postal_code': data.get('postal_code', ''),
        'country': data.get('country', ''),
        'tax_id': data.get('tax_id', ''),
        'payment_terms': data.get('payment_terms', 'Net 30'),
        'notes': data.get('notes', ''),
        'tenant_id': request.tenant_id,
        'created_at': now,
        'updated_at': now
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
        return jsonify({"error": f"Failed to create vendor: {str(e)}"}), 500

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
        
        query = "SELECT * FROM c WHERE c.tenant_id = @tenant_id"
        params = [{"name": "@tenant_id", "value": request.tenant_id}]
        if search_term:
            query += " AND CONTAINS(LOWER(c.name), @search)"
            params.append({"name": "@search", "value": search_term.lower()})
        
        query += " ORDER BY c.created_at DESC"
        
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
    data = request.get_json()
    
    # Validate data
    errors = validate_vendor_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing vendor
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
        
        # Update fields
        updatable_fields = [
            'name', 'contact_person', 'email', 'phone', 'address', 'city', 'state',
            'postal_code', 'country', 'tax_id', 'payment_terms', 'notes'
        ]
        
        for field in updatable_fields:
            if field in data:
                vendor[field] = data[field]
        
        vendor['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = vendors_container.replace_item(
            item=vendor['id'],
            body=vendor
        )

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
        return jsonify({"error": f"Failed to update vendor: {str(e)}"}), 500

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
