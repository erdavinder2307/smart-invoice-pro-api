from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import recurring_profiles_container
import uuid
from flasgger import swag_from
from datetime import datetime, timedelta
from enum import Enum

recurring_profiles_blueprint = Blueprint('recurring_profiles', __name__)

class RecurringStatus(Enum):
    Active = 'Active'
    Paused = 'Paused'
    Expired = 'Expired'
    Stopped = 'Stopped'

class FrequencyType(Enum):
    Daily = 'Daily'
    Weekly = 'Weekly'
    Monthly = 'Monthly'
    Quarterly = 'Quarterly'
    Yearly = 'Yearly'

def calculate_next_run_date(current_date, frequency):
    """Calculate the next run date based on frequency"""
    if frequency == 'Daily':
        return (datetime.fromisoformat(current_date.replace('Z', '+00:00')) + timedelta(days=1)).date().isoformat()
    elif frequency == 'Weekly':
        return (datetime.fromisoformat(current_date.replace('Z', '+00:00')) + timedelta(weeks=1)).date().isoformat()
    elif frequency == 'Monthly':
        current = datetime.fromisoformat(current_date.replace('Z', '+00:00'))
        # Add one month (handle different month lengths)
        next_month = current.month + 1
        next_year = current.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        try:
            return current.replace(year=next_year, month=next_month).date().isoformat()
        except ValueError:
            # Handle case where day doesn't exist in next month (e.g., Jan 31 -> Feb 31)
            # Use last day of the month instead
            if next_month == 2:
                last_day = 28 if next_year % 4 != 0 else 29
            elif next_month in [4, 6, 9, 11]:
                last_day = 30
            else:
                last_day = 31
            return current.replace(year=next_year, month=next_month, day=last_day).date().isoformat()
    elif frequency == 'Quarterly':
        current = datetime.fromisoformat(current_date.replace('Z', '+00:00'))
        return (current + timedelta(days=90)).date().isoformat()
    elif frequency == 'Yearly':
        current = datetime.fromisoformat(current_date.replace('Z', '+00:00'))
        try:
            return current.replace(year=current.year + 1).date().isoformat()
        except ValueError:
            # Handle Feb 29 on non-leap years
            return current.replace(year=current.year + 1, day=28).date().isoformat()
    else:
        return current_date

def validate_recurring_profile_data(data, is_update=False):
    """Validate recurring profile data"""
    errors = {}
    
    if not is_update:
        required_fields = ['profile_name', 'customer_id', 'frequency', 'start_date']
        for field in required_fields:
            if field not in data:
                errors[field] = f'{field} is required'
    
    # Validate status
    if 'status' in data and data['status'] not in RecurringStatus._value2member_map_:
        errors['status'] = f'Invalid status: {data["status"]}'
    
    # Validate frequency
    if 'frequency' in data and data['frequency'] not in FrequencyType._value2member_map_:
        errors['frequency'] = f'Invalid frequency: {data["frequency"]}'
    
    # Validate dates
    if 'start_date' in data and 'end_date' in data and data.get('end_date'):
        try:
            start = datetime.fromisoformat(data['start_date'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(data['end_date'].replace('Z', '+00:00'))
            if end <= start:
                errors['end_date'] = 'End date must be after start date'
        except ValueError:
            errors['dates'] = 'Invalid date format'
    
    return errors

@recurring_profiles_blueprint.route('/recurring-profiles', methods=['POST'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'profile_name': {'type': 'string'},
                    'customer_id': {'type': 'integer'},
                    'customer_name': {'type': 'string'},
                    'frequency': {'type': 'string', 'enum': ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Yearly']},
                    'start_date': {'type': 'string', 'format': 'date'},
                    'end_date': {'type': 'string', 'format': 'date'},
                    'occurrence_limit': {'type': 'integer'},
                    'occurrences_created': {'type': 'integer'},
                    'next_run_date': {'type': 'string', 'format': 'date'},
                    'last_run_date': {'type': 'string', 'format': 'date'},
                    'status': {'type': 'string', 'enum': ['Active', 'Paused', 'Expired', 'Stopped']},
                    'email_reminder': {'type': 'boolean'},
                    'items': {'type': 'array'},
                    'payment_terms': {'type': 'string'},
                    'notes': {'type': 'string'},
                    'terms_conditions': {'type': 'string'},
                    'is_gst_applicable': {'type': 'boolean'},
                    'cgst_amount': {'type': 'number'},
                    'sgst_amount': {'type': 'number'},
                    'igst_amount': {'type': 'number'}
                },
                'required': ['profile_name', 'customer_id', 'frequency', 'start_date']
            },
            'description': 'Recurring profile data'
        }
    ],
    'responses': {
        '201': {
            'description': 'Recurring profile created successfully'
        },
        '400': {
            'description': 'Validation error'
        }
    }
})
def create_recurring_profile():
    """Create a new recurring profile"""
    data = request.get_json()
    
    # Validate data
    errors = validate_recurring_profile_data(data)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    now = datetime.utcnow().isoformat()
    
    # Calculate next run date (default to start date if not provided)
    next_run_date = data.get('next_run_date', data['start_date'])
    
    item = {
        'id': str(uuid.uuid4()),
        'profile_name': data['profile_name'],
        'customer_id': data['customer_id'],
        'customer_name': data.get('customer_name', ''),
        'frequency': data['frequency'],
        'start_date': data['start_date'],
        'end_date': data.get('end_date', None),
        'occurrence_limit': data.get('occurrence_limit', None),
        'occurrences_created': data.get('occurrences_created', 0),
        'next_run_date': next_run_date,
        'last_run_date': data.get('last_run_date', None),
        'status': data.get('status', 'Active'),
        'email_reminder': data.get('email_reminder', False),
        'items': data.get('items', []),
        'payment_terms': data.get('payment_terms', ''),
        'notes': data.get('notes', ''),
        'terms_conditions': data.get('terms_conditions', ''),
        'is_gst_applicable': data.get('is_gst_applicable', False),
        'cgst_amount': data.get('cgst_amount', 0.0),
        'sgst_amount': data.get('sgst_amount', 0.0),
        'igst_amount': data.get('igst_amount', 0.0),
        'created_at': now,
        'updated_at': now
    }
    
    try:
        created_item = recurring_profiles_container.create_item(body=item)
        return jsonify(created_item), 201
    except Exception as e:
        return jsonify({"error": f"Failed to create recurring profile: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles', methods=['GET'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'status',
            'in': 'query',
            'type': 'string',
            'description': 'Filter by status'
        },
        {
            'name': 'customer_id',
            'in': 'query',
            'type': 'integer',
            'description': 'Filter by customer ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'List of recurring profiles'
        }
    }
})
def get_recurring_profiles():
    """Get all recurring profiles with optional filters"""
    try:
        status_filter = request.args.get('status')
        customer_id_filter = request.args.get('customer_id', type=int)
        
        query = "SELECT * FROM c"
        conditions = []
        
        if status_filter:
            conditions.append(f"c.status = '{status_filter}'")
        if customer_id_filter:
            conditions.append(f"c.customer_id = {customer_id_filter}")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY c.created_at DESC"
        
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        return jsonify(items), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch recurring profiles: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['GET'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'profile_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Profile ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile details'
        },
        '404': {
            'description': 'Profile not found'
        }
    }
})
def get_recurring_profile(profile_id):
    """Get a specific recurring profile by ID"""
    try:
        query = f"SELECT * FROM c WHERE c.id = '{profile_id}'"
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Recurring profile not found"}), 404
        
        return jsonify(items[0]), 200
    except Exception as e:
        return jsonify({"error": f"Failed to fetch recurring profile: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['PUT'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'profile_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Profile ID'
        },
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object'
            },
            'description': 'Fields to update'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile updated successfully'
        },
        '404': {
            'description': 'Profile not found'
        }
    }
})
def update_recurring_profile(profile_id):
    """Update an existing recurring profile"""
    data = request.get_json()
    
    # Validate data
    errors = validate_recurring_profile_data(data, is_update=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    
    try:
        # Fetch existing profile
        query = f"SELECT * FROM c WHERE c.id = '{profile_id}'"
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Recurring profile not found"}), 404
        
        existing_profile = items[0]
        
        # Update fields
        for key, value in data.items():
            if key != 'id' and key != 'created_at':
                existing_profile[key] = value
        
        existing_profile['updated_at'] = datetime.utcnow().isoformat()
        
        # Replace the item
        updated_item = recurring_profiles_container.replace_item(
            item=existing_profile['id'],
            body=existing_profile
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to update recurring profile: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>', methods=['DELETE'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'profile_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Profile ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile deleted successfully'
        },
        '404': {
            'description': 'Profile not found'
        }
    }
})
def delete_recurring_profile(profile_id):
    """Delete a recurring profile"""
    try:
        # Fetch existing profile to get partition key
        query = f"SELECT * FROM c WHERE c.id = '{profile_id}'"
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Recurring profile not found"}), 404
        
        profile = items[0]
        
        # Delete the profile
        recurring_profiles_container.delete_item(
            item=profile_id,
            partition_key=profile['customer_id']
        )
        
        return jsonify({"message": "Recurring profile deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to delete recurring profile: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/pause', methods=['POST'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'profile_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Profile ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile paused successfully'
        }
    }
})
def pause_recurring_profile(profile_id):
    """Pause a recurring profile"""
    try:
        query = f"SELECT * FROM c WHERE c.id = '{profile_id}'"
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Recurring profile not found"}), 404
        
        profile = items[0]
        profile['status'] = 'Paused'
        profile['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = recurring_profiles_container.replace_item(
            item=profile['id'],
            body=profile
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to pause profile: {str(e)}"}), 500

@recurring_profiles_blueprint.route('/recurring-profiles/<profile_id>/resume', methods=['POST'])
@swag_from({
    'tags': ['Recurring Profiles'],
    'parameters': [
        {
            'name': 'profile_id',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Profile ID'
        }
    ],
    'responses': {
        '200': {
            'description': 'Profile resumed successfully'
        }
    }
})
def resume_recurring_profile(profile_id):
    """Resume a paused recurring profile"""
    try:
        query = f"SELECT * FROM c WHERE c.id = '{profile_id}'"
        items = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if not items:
            return jsonify({"error": "Recurring profile not found"}), 404
        
        profile = items[0]
        profile['status'] = 'Active'
        profile['updated_at'] = datetime.utcnow().isoformat()
        
        updated_item = recurring_profiles_container.replace_item(
            item=profile['id'],
            body=profile
        )
        
        return jsonify(updated_item), 200
    except Exception as e:
        return jsonify({"error": f"Failed to resume profile: {str(e)}"}), 500
