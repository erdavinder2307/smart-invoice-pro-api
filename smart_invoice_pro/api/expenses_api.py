from flask import Blueprint, request, jsonify
from flasgger import swag_from
import uuid
from datetime import datetime
import os
import base64
from werkzeug.utils import secure_filename

from smart_invoice_pro.utils.cosmos_client import expenses_container

expenses_blueprint = Blueprint('expenses', __name__)

# Allowed file extensions for receipts
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf', 'gif'}
UPLOAD_FOLDER = 'uploads/receipts'

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─────────────────────────────────────────────────────────────────────────────
# CREATE EXPENSE
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses', methods=['POST'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Create a new expense',
    'parameters': [{
        'name': 'body',
        'in': 'body',
        'required': True,
        'schema': {
            'type': 'object',
            'properties': {
                'vendor_name': {'type': 'string'},
                'date': {'type': 'string', 'format': 'date'},
                'category': {'type': 'string'},
                'amount': {'type': 'number'},
                'currency': {'type': 'string', 'default': 'INR'},
                'notes': {'type': 'string'},
                'receipt_base64': {'type': 'string'},
                'receipt_filename': {'type': 'string'}
            },
            'required': ['vendor_name', 'date', 'category', 'amount']
        }
    }],
    'responses': {
        201: {'description': 'Expense created successfully'},
        400: {'description': 'Invalid input'}
    }
})
def create_expense():
    try:
        data = request.get_json()
        
        # Generate unique ID
        expense_id = str(uuid.uuid4())
        
        # Validate required fields
        required = ['vendor_name', 'date', 'category', 'amount']
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Handle receipt upload if provided
        receipt_url = None
        if 'receipt_base64' in data and data['receipt_base64']:
            try:
                receipt_data = data['receipt_base64']
                receipt_filename = data.get('receipt_filename', 'receipt')
                
                # Remove data URL prefix if present
                if ',' in receipt_data:
                    receipt_data = receipt_data.split(',')[1]
                
                # Decode base64
                file_bytes = base64.b64decode(receipt_data)
                
                # Generate safe filename
                safe_filename = secure_filename(f"{expense_id}_{receipt_filename}")
                file_path = os.path.join(UPLOAD_FOLDER, safe_filename)
                
                # Save file
                with open(file_path, 'wb') as f:
                    f.write(file_bytes)
                
                receipt_url = f"/uploads/receipts/{safe_filename}"
            except Exception as e:
                print(f"Error saving receipt: {str(e)}")
                # Continue without receipt if upload fails
        
        # Create expense document
        expense = {
            'id': expense_id,
            'vendor_name': data['vendor_name'],
            'date': data['date'],
            'category': data['category'],
            'amount': float(data['amount']),
            'currency': data.get('currency', 'INR'),
            'notes': data.get('notes', ''),
            'receipt_url': receipt_url,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        # Save to Cosmos DB
        expenses_container.create_item(body=expense)
        
        return jsonify(expense), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create expense: {str(e)}"}), 500

# ─────────────────────────────────────────────────────────────────────────────
# GET ALL EXPENSES
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses', methods=['GET'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Get all expenses',
    'parameters': [
        {
            'name': 'category',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by category'
        },
        {
            'name': 'start_date',
            'in': 'query',
            'type': 'string',
            'format': 'date',
            'description': 'Filter by start date'
        },
        {
            'name': 'end_date',
            'in': 'query',
            'type': 'string',
            'format': 'date',
            'description': 'Filter by end date'
        }
    ],
    'responses': {
        200: {'description': 'List of expenses'}
    }
})
def get_expenses():
    try:
        # Get query parameters
        category = request.args.get('category')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Build query
        query = "SELECT * FROM c WHERE 1=1"
        parameters = []
        
        if category:
            query += " AND c.category = @category"
            parameters.append({"name": "@category", "value": category})
        
        if start_date:
            query += " AND c.date >= @start_date"
            parameters.append({"name": "@start_date", "value": start_date})
        
        if end_date:
            query += " AND c.date <= @end_date"
            parameters.append({"name": "@end_date", "value": end_date})
        
        query += " ORDER BY c.date DESC"
        
        # Execute query
        items = list(expenses_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch expenses: {str(e)}"}), 500

# ─────────────────────────────────────────────────────────────────────────────
# GET EXPENSE BY ID
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses/<expense_id>', methods=['GET'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Get expense by ID',
    'parameters': [{
        'name': 'expense_id',
        'in': 'path',
        'type': 'string',
        'required': True
    }],
    'responses': {
        200: {'description': 'Expense details'},
        404: {'description': 'Expense not found'}
    }
})
def get_expense(expense_id):
    try:
        query = "SELECT * FROM c WHERE c.id = @id"
        parameters = [{"name": "@id", "value": expense_id}]
        
        items = list(expenses_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Expense not found"}), 404
        
        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch expense: {str(e)}"}), 500

# ─────────────────────────────────────────────────────────────────────────────
# UPDATE EXPENSE
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses/<expense_id>', methods=['PUT'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Update an expense',
    'parameters': [
        {
            'name': 'expense_id',
            'in': 'path',
            'type': 'string',
            'required': True
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'vendor_name': {'type': 'string'},
                    'date': {'type': 'string', 'format': 'date'},
                    'category': {'type': 'string'},
                    'amount': {'type': 'number'},
                    'currency': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'receipt_base64': {'type': 'string'},
                    'receipt_filename': {'type': 'string'}
                }
            }
        }
    ],
    'responses': {
        200: {'description': 'Expense updated successfully'},
        404: {'description': 'Expense not found'}
    }
})
def update_expense(expense_id):
    try:
        # Fetch existing expense
        query = "SELECT * FROM c WHERE c.id = @id"
        parameters = [{"name": "@id", "value": expense_id}]
        
        items = list(expenses_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Expense not found"}), 404
        
        expense = items[0]
        data = request.get_json()
        
        # Update fields
        expense['vendor_name'] = data.get('vendor_name', expense['vendor_name'])
        expense['date'] = data.get('date', expense['date'])
        expense['category'] = data.get('category', expense['category'])
        expense['amount'] = float(data.get('amount', expense['amount']))
        expense['currency'] = data.get('currency', expense['currency'])
        expense['notes'] = data.get('notes', expense['notes'])
        expense['updated_at'] = datetime.utcnow().isoformat()
        
        # Handle new receipt upload
        if 'receipt_base64' in data and data['receipt_base64']:
            try:
                receipt_data = data['receipt_base64']
                receipt_filename = data.get('receipt_filename', 'receipt')
                
                # Remove data URL prefix if present
                if ',' in receipt_data:
                    receipt_data = receipt_data.split(',')[1]
                
                # Decode base64
                file_bytes = base64.b64decode(receipt_data)
                
                # Generate safe filename
                safe_filename = secure_filename(f"{expense_id}_{receipt_filename}")
                file_path = os.path.join(UPLOAD_FOLDER, safe_filename)
                
                # Delete old receipt if exists
                if expense.get('receipt_url'):
                    old_file_path = expense['receipt_url'].lstrip('/')
                    if os.path.exists(old_file_path):
                        os.remove(old_file_path)
                
                # Save new file
                with open(file_path, 'wb') as f:
                    f.write(file_bytes)
                
                expense['receipt_url'] = f"/uploads/receipts/{safe_filename}"
            except Exception as e:
                print(f"Error updating receipt: {str(e)}")
        
        # Replace in Cosmos DB
        expenses_container.replace_item(item=expense['id'], body=expense)
        
        return jsonify(expense), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update expense: {str(e)}"}), 500

# ─────────────────────────────────────────────────────────────────────────────
# DELETE EXPENSE
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses/<expense_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Delete an expense',
    'parameters': [{
        'name': 'expense_id',
        'in': 'path',
        'type': 'string',
        'required': True
    }],
    'responses': {
        200: {'description': 'Expense deleted successfully'},
        404: {'description': 'Expense not found'}
    }
})
def delete_expense(expense_id):
    try:
        # Fetch expense to get receipt URL
        query = "SELECT * FROM c WHERE c.id = @id"
        parameters = [{"name": "@id", "value": expense_id}]
        
        items = list(expenses_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Expense not found"}), 404
        
        expense = items[0]
        
        # Delete receipt file if exists
        if expense.get('receipt_url'):
            file_path = expense['receipt_url'].lstrip('/')
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Error deleting receipt file: {str(e)}")
        
        # Delete from Cosmos DB
        expenses_container.delete_item(item=expense_id, partition_key=expense_id)
        
        return jsonify({"message": "Expense deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete expense: {str(e)}"}), 500

# ─────────────────────────────────────────────────────────────────────────────
# GET EXPENSE STATISTICS
# ─────────────────────────────────────────────────────────────────────────────
@expenses_blueprint.route('/expenses/stats/summary', methods=['GET'])
@swag_from({
    'tags': ['Expenses'],
    'summary': 'Get expense statistics',
    'parameters': [
        {
            'name': 'start_date',
            'in': 'query',
            'type': 'string',
            'format': 'date'
        },
        {
            'name': 'end_date',
            'in': 'query',
            'type': 'string',
            'format': 'date'
        }
    ],
    'responses': {
        200: {'description': 'Expense statistics'}
    }
})
def get_expense_stats():
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Build query
        query = "SELECT * FROM c WHERE 1=1"
        parameters = []
        
        if start_date:
            query += " AND c.date >= @start_date"
            parameters.append({"name": "@start_date", "value": start_date})
        
        if end_date:
            query += " AND c.date <= @end_date"
            parameters.append({"name": "@end_date", "value": end_date})
        
        # Get all expenses
        expenses = list(expenses_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        # Calculate statistics
        total_amount = sum(exp['amount'] for exp in expenses)
        total_count = len(expenses)
        
        # Group by category
        by_category = {}
        for exp in expenses:
            cat = exp['category']
            if cat not in by_category:
                by_category[cat] = {'count': 0, 'amount': 0}
            by_category[cat]['count'] += 1
            by_category[cat]['amount'] += exp['amount']
        
        stats = {
            'total_amount': total_amount,
            'total_count': total_count,
            'by_category': by_category,
            'average_amount': total_amount / total_count if total_count > 0 else 0
        }
        
        return jsonify(stats), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch statistics: {str(e)}"}), 500
