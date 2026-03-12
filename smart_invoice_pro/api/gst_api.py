from flask import Blueprint, jsonify
import requests
import re
import os
from flasgger import swag_from

gst_blueprint = Blueprint('gst', __name__)

# GST Suvidha Provider API configuration
GST_API_BASE_URL = os.getenv('GST_API_BASE_URL', 'https://api.gstsystem.co.in/gst/v1')
GST_API_KEY = os.getenv('GST_API_KEY', '')
GST_API_TIMEOUT = float(os.getenv('GST_API_TIMEOUT', '10'))

def validate_gstin_format(gstin):
    """Validate GSTIN format: 2-digit state code + 10-char PAN + check digit + Z + alpha-numeric"""
    if not gstin:
        return False
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'
    return bool(re.match(pattern, gstin.upper()))

def map_taxpayer_type_to_gst_treatment(taxpayer_type):
    """Map taxpayer type from GST API to internal GST treatment codes"""
    mapping = {
        'Regular': 'regular',
        'Composition': 'composition',
        'Unregistered': 'unregistered',
        'Consumer': 'consumer',
        'SEZ': 'special_economic_zone',
        'Deemed Export': 'deemed_export',
    }
    return mapping.get(taxpayer_type, 'regular')

def extract_state_from_gstin(gstin):
    """Extract state code from GSTIN and map to state name"""
    state_codes = {
        '01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
        '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
        '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
        '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
        '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram',
        '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
        '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
        '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
        '25': 'Daman & Diu', '26': 'Dadra & Nagar Haveli', '27': 'Maharashtra',
        '28': 'Andhra Pradesh', '29': 'Karnataka', '30': 'Goa',
        '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu',
        '34': 'Puducherry', '35': 'Andaman & Nicobar Islands', '36': 'Telangana',
        '37': 'Andhra Pradesh', '38': 'Ladakh',
    }
    state_code = gstin[:2]
    return state_codes.get(state_code, 'Gujarat')  # Default to Gujarat if not found

def normalize_provider_response(gstin, payload):
    """Normalize provider payload into frontend-consumable structure."""
    if not isinstance(payload, dict):
        return None

    primary_address = (payload.get('pradr') or {}).get('addr') or {}

    legal_name = payload.get('legal_name') or payload.get('lgnm') or payload.get('company_name')
    trade_name = payload.get('trade_name') or payload.get('tradeNam') or payload.get('trade_name_of_business')
    taxpayer_type = payload.get('taxpayer_type') or payload.get('dty') or 'Regular'
    state = payload.get('state') or primary_address.get('st') or extract_state_from_gstin(gstin)

    building = primary_address.get('bno')
    street = primary_address.get('st')
    location = primary_address.get('loc')
    pincode = str(primary_address.get('pncd') or payload.get('pincode') or '')

    address = payload.get('address')
    if not address:
        address_parts = [part for part in [building, street, location, state, pincode] if part]
        address = ', '.join(address_parts)

    if not legal_name and not trade_name and not address:
        return None

    return {
        'gstin': gstin,
        'legal_name': legal_name or '',
        'trade_name': trade_name or legal_name or '',
        'address': address or '',
        'state': state,
        'taxpayer_type': taxpayer_type,
        'gst_treatment': map_taxpayer_type_to_gst_treatment(taxpayer_type),
        'city': payload.get('city') or location or '',
        'pincode': pincode,
        'business_type': payload.get('business_type') or payload.get('ctb') or '',
    }

@gst_blueprint.route('/gst/prefill/<gstin>', methods=['GET'])
@swag_from({
    'parameters': [
        {
            'name': 'gstin',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Valid 15-character GSTIN'
        }
    ],
    'responses': {
        '200': {
            'description': 'GST details fetched successfully',
            'schema': {
                'type': 'object',
                'properties': {
                    'success': {'type': 'boolean'},
                    'data': {
                        'type': 'object',
                        'properties': {
                            'gstin': {'type': 'string'},
                            'legal_name': {'type': 'string'},
                            'trade_name': {'type': 'string'},
                            'address': {'type': 'string'},
                            'state': {'type': 'string'},
                            'taxpayer_type': {'type': 'string'},
                            'gst_treatment': {'type': 'string'},
                        }
                    }
                }
            }
        },
        '400': {'description': 'Invalid GSTIN format'},
        '404': {'description': 'GSTIN not found'},
        '500': {'description': 'API error'}
    }
})
def prefill_gst_details(gstin):
    """
    Fetch GST details from GST Suvidha Provider API
    """
    try:
        # Validate GSTIN format
        gstin = gstin.upper().strip()
        if not validate_gstin_format(gstin):
            return jsonify({
                'success': False,
                'error': 'Invalid GSTIN format. Please provide a valid 15-character GSTIN.'
            }), 400

        if GST_API_KEY:
            headers = {
                'Authorization': f'Bearer {GST_API_KEY}',
                'Content-Type': 'application/json',
            }
            response = requests.get(
                f'{GST_API_BASE_URL}/search/{gstin}',
                headers=headers,
                timeout=GST_API_TIMEOUT,
            )

            if response.status_code == 404:
                return jsonify({
                    'success': False,
                    'error': 'GSTIN not found in government records.'
                }), 404

            if response.status_code != 200:
                return jsonify({
                    'success': False,
                    'error': 'Failed to fetch GST details. Please try again later.'
                }), 500

            provider_data = normalize_provider_response(gstin, response.json())
            if not provider_data:
                return jsonify({
                    'success': False,
                    'error': 'GSTIN not found in government records.'
                }), 404

            return jsonify({
                'success': True,
                'data': provider_data
            }), 200

        # Mock fallback for local development when GST_API_KEY is not configured.
        state = extract_state_from_gstin(gstin)
        mock_data = {
            'gstin': gstin,
            'legal_name': f'Demo Private Limited ({gstin[:10]})',
            'trade_name': f'Demo Trading Co ({gstin[:10]})',
            'address': f'123, Sample Street, Business District, {state} - 400001',
            'state': state,
            'taxpayer_type': 'Regular',
            'gst_treatment': map_taxpayer_type_to_gst_treatment('Regular'),
            'city': 'Mumbai',
            'pincode': '400001',
            'business_type': 'Private Limited Company',
        }

        return jsonify({
            'success': True,
            'data': mock_data
        }), 200

    except requests.exceptions.Timeout:
        return jsonify({
            'success': False,
            'error': 'Request timeout. GST service is taking too long to respond.'
        }), 500
    except requests.exceptions.RequestException as e:
        return jsonify({
            'success': False,
            'error': f'Failed to connect to GST service: {str(e)}'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'An unexpected error occurred: {str(e)}'
        }), 500

@gst_blueprint.route('/gst/validate/<gstin>', methods=['GET'])
def validate_gstin(gstin):
    """
    Simple GSTIN format validation endpoint
    """
    gstin = gstin.upper().strip()
    is_valid = validate_gstin_format(gstin)
    
    return jsonify({
        'success': True,
        'valid': is_valid,
        'gstin': gstin
    }), 200
