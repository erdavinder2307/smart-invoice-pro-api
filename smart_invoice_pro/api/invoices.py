from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import container
import uuid

api_blueprint = Blueprint('api', __name__)

@api_blueprint.route('/invoices', methods=['POST'])
def create_invoice():
    data = request.get_json()
    item = {
        'id': str(uuid.uuid4()),
        'customer_id': data['customer_id'],
        'amount': data['amount'],
        'description': data.get('description', '')
    }
    container.create_item(body=item)
    return jsonify(item), 201

@api_blueprint.route('/invoices/<customer_id>', methods=['GET'])
def get_invoices(customer_id):
    query = f"SELECT * FROM c WHERE c.customer_id = '{customer_id}'"
    items = list(container.query_items(query=query, enable_cross_partition_query=True))
    return jsonify(items)
