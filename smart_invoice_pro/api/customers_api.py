from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import customers_container
import uuid
from flasgger import swag_from
from datetime import datetime

customers_blueprint = Blueprint('customers', __name__)

@customers_blueprint.route('/customers', methods=['POST'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'address': {'type': 'string'},
                    'gst_number': {'type': 'string'}
                },
                'required': ['name', 'email']
            },
            'description': 'Customer data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Customer created',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'name': 'John Doe',
                    'email': 'john@example.com',
                    'phone': '1234567890',
                    'address': '123 Main St',
                    'gst_number': 'GST123',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        }
    }
})
def create_customer():
    data = request.get_json()
    now = datetime.utcnow().isoformat()
    item = {
        'id': str(uuid.uuid4()),
        'customer_id': str(uuid.uuid4()),  # Unique customer ID
        'name': data['name'],
        'email': data['email'],
        'phone': data.get('phone', ''),
        'address': data.get('address', ''),
        'gst_number': data.get('gst_number', ''),
        'created_at': now,
        'updated_at': now
    }
    customers_container.create_item(body=item)
    return jsonify(item), 201

@customers_blueprint.route('/customers', methods=['GET'])
@swag_from({
    'tags': ['Customers'],
    'responses': {
        '200': {
            'description': 'List of all customers',
            'examples': {
                'application/json': [
                    {
                        'id': 'uuid',
                        'name': 'John Doe',
                        'email': 'john@example.com',
                        'phone': '1234567890',
                        'address': '123 Main St',
                        'gst_number': 'GST123',
                        'created_at': '2025-06-05T12:00:00Z',
                        'updated_at': '2025-06-05T12:00:00Z'
                    }
                ]
            }
        }
    }
})
def list_customers():
    items = list(customers_container.read_all_items())
    return jsonify(items)

@customers_blueprint.route('/customers/<customer_id>', methods=['GET'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer details',
            'examples': {
                'application/json': {
                    'id': 'uuid',
                    'name': 'John Doe',
                    'email': 'john@example.com',
                    'phone': '1234567890',
                    'address': '123 Main St',
                    'gst_number': 'GST123',
                    'created_at': '2025-06-05T12:00:00Z',
                    'updated_at': '2025-06-05T12:00:00Z'
                }
            }
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def get_customer(customer_id):
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    return jsonify(items[0])

@customers_blueprint.route('/customers/<customer_id>', methods=['PUT'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'address': {'type': 'string'},
                    'gst_number': {'type': 'string'}
                }
            },
            'description': 'Customer data to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer updated',
            'examples': {'application/json': {'id': 'uuid', 'name': 'John Doe', 'email': 'john@example.com', 'phone': '1234567890', 'address': '123 Main St', 'gst_number': 'GST123', 'created_at': '2025-06-05T12:00:00Z', 'updated_at': '2025-06-05T12:00:00Z'}}
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def update_customer(customer_id):
    data = request.get_json()
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    for field in ['name', 'email', 'phone', 'address', 'gst_number']:
        if field in data:
            item[field] = data[field]
    item['updated_at'] = datetime.utcnow().isoformat()
    customers_container.replace_item(item=item['id'], body=item)
    return jsonify(item)

@customers_blueprint.route('/customers/<customer_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Customers'],
    'parameters': [
        {
            'name': 'customer_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Customer deleted',
            'examples': {'application/json': {'message': 'Customer deleted'}}
        },
        '404': {
            'description': 'Customer not found',
            'examples': {'application/json': {'error': 'Customer not found'}}
        }
    }
})
def delete_customer(customer_id):
    query = f"SELECT * FROM c WHERE c.id = '{customer_id}'"
    items = list(customers_container.query_items(query=query, enable_cross_partition_query=True))
    if not items:
        return jsonify({'error': 'Customer not found'}), 404
    item = items[0]
    # Cosmos DB partition key for customers is /customer_id, so use the value of 'customer_id' from the item
    customers_container.delete_item(item=item['id'], partition_key=item['customer_id'])
    return jsonify({'message': 'Customer deleted'})
