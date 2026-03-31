"""
Tax Rates & GST Calculation Engine
===================================
GET    /api/settings/taxes               – list all active tax rates for tenant
POST   /api/settings/taxes               – create a new tax rate (Admin)
PUT    /api/settings/taxes/<id>          – update a tax rate (Admin)
DELETE /api/settings/taxes/<id>          – soft-delete (Admin)
POST   /api/invoices/calculate-tax       – calculate CGST/SGST/IGST for a set of items

Tax rate document schema (container: "tax_rates", partition: /tenant_id):
{
    "id":          "<uuid>",
    "tenant_id":   "<tenant_id>",
    "name":        "GST 18%",
    "rate":        18.0,          # Total GST rate (e.g. 18 for 18%)
    "type":        "GST",         # "GST" | "CESS" | "Exempt"
    "components": {
        "cgst": 9.0,
        "sgst": 9.0,
        "igst": 18.0
    },
    "is_active":   true,
    "is_default":  false,
    "created_at":  "...",
    "updated_at":  "..."
}
"""

import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify

from smart_invoice_pro.utils.cosmos_client import get_container
from smart_invoice_pro.api.roles_api import require_role
from smart_invoice_pro.api.gst_api import extract_state_from_gstin, validate_gstin_format

tax_rates_blueprint = Blueprint('tax_rates', __name__)

# ── Default Indian GST rate slab seeds ───────────────────────────────────────
DEFAULT_GST_SLABS = [
    {'name': 'Exempt (0%)',  'rate': 0.0,  'type': 'Exempt',
     'components': {'cgst': 0.0,  'sgst': 0.0,  'igst': 0.0},  'is_default': False},
    {'name': 'GST 5%',       'rate': 5.0,  'type': 'GST',
     'components': {'cgst': 2.5,  'sgst': 2.5,  'igst': 5.0},  'is_default': False},
    {'name': 'GST 12%',      'rate': 12.0, 'type': 'GST',
     'components': {'cgst': 6.0,  'sgst': 6.0,  'igst': 12.0}, 'is_default': False},
    {'name': 'GST 18%',      'rate': 18.0, 'type': 'GST',
     'components': {'cgst': 9.0,  'sgst': 9.0,  'igst': 18.0}, 'is_default': True},
    {'name': 'GST 28%',      'rate': 28.0, 'type': 'GST',
     'components': {'cgst': 14.0, 'sgst': 14.0, 'igst': 28.0}, 'is_default': False},
    {'name': 'GST 28% + CESS 12%', 'rate': 28.0, 'type': 'CESS',
     'components': {'cgst': 14.0, 'sgst': 14.0, 'igst': 28.0}, 'is_default': False},
]


def _get_tax_rates_container():
    return get_container("tax_rates", "/tenant_id")


def _seed_default_rates(tenant_id: str) -> list:
    """Insert default GST slabs for a new tenant and return them."""
    container = _get_tax_rates_container()
    now = datetime.utcnow().isoformat()
    seeded = []
    for slab in DEFAULT_GST_SLABS:
        doc = {
            'id':         str(uuid.uuid4()),
            'tenant_id':  tenant_id,
            'name':       slab['name'],
            'rate':       slab['rate'],
            'type':       slab['type'],
            'components': slab['components'],
            'is_default': slab['is_default'],
            'is_active':  True,
            'created_at': now,
            'updated_at': now,
        }
        container.create_item(body=doc)
        seeded.append(doc)
    return seeded


def _list_rates(tenant_id: str) -> list:
    container = _get_tax_rates_container()
    items = list(container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid AND c.is_active = true ORDER BY c.rate ASC",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    return items


def get_tax_rates_for_tenant(tenant_id: str) -> list:
    """Public helper used by invoice calculation engine."""
    rates = _list_rates(tenant_id)
    if not rates:
        rates = _seed_default_rates(tenant_id)
    return rates


# ── GST Calculation Engine ────────────────────────────────────────────────────

def calculate_gst(items: list, seller_state: str, customer_state: str,
                  gst_treatment: str, is_gst_applicable: bool,
                  place_of_supply: str = None) -> dict:
    """
    Core GST calculation.

    Rules:
    - Not GST-applicable → all zeros
    - SEZ / deemed_export / export → zero-rated supply (IGST=0 but claimable refund)
    - Composition dealer customer → no GST charged on invoice
    - Intra-state (seller_state == customer_state) → CGST + SGST
    - Inter-state → IGST

    Returns:
        {
            items_with_tax: [...],     # items with cgst/sgst/igst per line
            cgst_amount:  float,
            sgst_amount:  float,
            igst_amount:  float,
            total_tax:    float,
            is_intra_state: bool,
            tax_type:     "CGST_SGST" | "IGST" | "NONE"
        }
    """
    zero = {'items_with_tax': items, 'cgst_amount': 0.0, 'sgst_amount': 0.0,
            'igst_amount': 0.0, 'total_tax': 0.0, 'is_intra_state': None, 'tax_type': 'NONE'}

    if not is_gst_applicable:
        return zero

    # Zero-rated supplies
    ZERO_RATED = {'special_economic_zone', 'deemed_export', 'export', 'consumer'}
    if gst_treatment in ZERO_RATED:
        return {**zero, 'tax_type': 'NONE'}

    # Composition scheme: no GST charged on invoice
    if gst_treatment == 'composition':
        return {**zero, 'tax_type': 'NONE'}

    # Determine intra vs inter state
    effective_customer_state = place_of_supply or customer_state or ''
    intra_state = bool(
        seller_state and effective_customer_state
        and seller_state.strip().lower() == effective_customer_state.strip().lower()
    )

    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0
    items_with_tax = []

    for item in items:
        qty = float(item.get('quantity', 0) or 0)
        rate = float(item.get('rate', 0) or 0)
        discount = float(item.get('discount', 0) or 0)
        tax_rate = float(item.get('tax', 0) or 0)          # e.g. 18 for 18%
        base_amount = max(0.0, qty * rate - discount)

        if intra_state:
            item_cgst = round(base_amount * (tax_rate / 2) / 100, 2)
            item_sgst = round(base_amount * (tax_rate / 2) / 100, 2)
            item_igst = 0.0
        else:
            item_cgst = 0.0
            item_sgst = 0.0
            item_igst = round(base_amount * tax_rate / 100, 2)

        total_cgst += item_cgst
        total_sgst += item_sgst
        total_igst += item_igst

        items_with_tax.append({
            **item,
            'cgst': item_cgst,
            'sgst': item_sgst,
            'igst': item_igst,
        })

    total_cgst = round(total_cgst, 2)
    total_sgst = round(total_sgst, 2)
    total_igst = round(total_igst, 2)
    total_tax = round(total_cgst + total_sgst + total_igst, 2)

    return {
        'items_with_tax': items_with_tax,
        'cgst_amount':    total_cgst,
        'sgst_amount':    total_sgst,
        'igst_amount':    total_igst,
        'total_tax':      total_tax,
        'is_intra_state': intra_state,
        'tax_type':       'CGST_SGST' if intra_state else 'IGST',
    }


def _get_seller_state(tenant_id: str) -> str:
    """Fetch seller's state from org profile (GSTIN → state, or address.state)."""
    from smart_invoice_pro.utils.cosmos_client import settings_container
    doc_id = f"{tenant_id}:organization_profile"
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return ''
    profile = items[0]
    gstin = profile.get('gstin', '')
    if gstin and validate_gstin_format(gstin):
        return extract_state_from_gstin(gstin)
    return (profile.get('address') or {}).get('state', '')


def _get_customer_state(tenant_id: str, customer_id: str) -> tuple:
    """Return (billing_state, gst_treatment, place_of_supply) for a customer."""
    from smart_invoice_pro.utils.cosmos_client import get_container as _gc
    customers_container = _gc("customers", "/tenant_id")
    items = list(customers_container.query_items(
        query="SELECT * FROM c WHERE c.id = @cid AND c.tenant_id = @tid",
        parameters=[
            {"name": "@cid", "value": str(customer_id)},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return '', 'regular', ''
    cust = items[0]
    return (
        cust.get('billing_state', '') or cust.get('place_of_supply', ''),
        cust.get('gst_treatment', 'regular'),
        cust.get('place_of_supply', ''),
    )


# ── API Endpoints ─────────────────────────────────────────────────────────────

@tax_rates_blueprint.route('/settings/taxes', methods=['GET'])
def list_tax_rates():
    """List all active tax rates for the current tenant. Seeds defaults on first call."""
    try:
        rates = get_tax_rates_for_tenant(request.tenant_id)
        return jsonify(rates), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tax_rates_blueprint.route('/settings/taxes', methods=['POST'])
@require_role('Admin')
def create_tax_rate():
    """Create a new tax rate (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        rate = data.get('rate')
        tax_type = (data.get('type') or 'GST').strip()

        if not name:
            return jsonify({'error': 'name is required'}), 400
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return jsonify({'error': 'rate must be a number'}), 400
        if not (0 <= rate <= 100):
            return jsonify({'error': 'rate must be between 0 and 100'}), 400
        if tax_type not in ('GST', 'CESS', 'Exempt'):
            return jsonify({'error': 'type must be GST, CESS, or Exempt'}), 400

        # Auto-derive components for GST type if not supplied
        components_in = data.get('components') or {}
        if tax_type == 'GST' and not components_in:
            components = {'cgst': rate / 2, 'sgst': rate / 2, 'igst': rate}
        elif tax_type == 'Exempt':
            components = {'cgst': 0.0, 'sgst': 0.0, 'igst': 0.0}
        else:
            components = {
                'cgst': float(components_in.get('cgst', rate / 2)),
                'sgst': float(components_in.get('sgst', rate / 2)),
                'igst': float(components_in.get('igst', rate)),
            }

        now = datetime.utcnow().isoformat()
        doc = {
            'id':         str(uuid.uuid4()),
            'tenant_id':  request.tenant_id,
            'name':       name,
            'rate':       rate,
            'type':       tax_type,
            'components': components,
            'is_default': bool(data.get('is_default', False)),
            'is_active':  True,
            'created_at': now,
            'updated_at': now,
        }
        _get_tax_rates_container().create_item(body=doc)
        return jsonify(doc), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tax_rates_blueprint.route('/settings/taxes/<rate_id>', methods=['PUT'])
@require_role('Admin')
def update_tax_rate(rate_id):
    """Update an existing tax rate (Admin only)."""
    try:
        container = _get_tax_rates_container()
        items = list(container.query_items(
            query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
            parameters=[
                {"name": "@id",  "value": rate_id},
                {"name": "@tid", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        if not items:
            return jsonify({'error': 'Tax rate not found'}), 404

        existing = items[0]
        data = request.get_json(silent=True) or {}

        name = (data.get('name') or existing['name']).strip()
        if not name:
            return jsonify({'error': 'name is required'}), 400

        rate = data.get('rate', existing['rate'])
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return jsonify({'error': 'rate must be a number'}), 400
        if not (0 <= rate <= 100):
            return jsonify({'error': 'rate must be between 0 and 100'}), 400

        tax_type = (data.get('type') or existing['type']).strip()
        if tax_type not in ('GST', 'CESS', 'Exempt'):
            return jsonify({'error': 'type must be GST, CESS, or Exempt'}), 400

        components_in = data.get('components') or existing.get('components', {})
        if tax_type == 'Exempt':
            components = {'cgst': 0.0, 'sgst': 0.0, 'igst': 0.0}
        else:
            components = {
                'cgst': float(components_in.get('cgst', rate / 2)),
                'sgst': float(components_in.get('sgst', rate / 2)),
                'igst': float(components_in.get('igst', rate)),
            }

        existing.update({
            'name':       name,
            'rate':       rate,
            'type':       tax_type,
            'components': components,
            'is_default': bool(data.get('is_default', existing.get('is_default', False))),
            'updated_at': datetime.utcnow().isoformat(),
        })
        container.upsert_item(existing)
        return jsonify(existing), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tax_rates_blueprint.route('/settings/taxes/<rate_id>', methods=['DELETE'])
@require_role('Admin')
def delete_tax_rate(rate_id):
    """Soft-delete a tax rate (Admin only)."""
    try:
        container = _get_tax_rates_container()
        items = list(container.query_items(
            query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
            parameters=[
                {"name": "@id",  "value": rate_id},
                {"name": "@tid", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        if not items:
            return jsonify({'error': 'Tax rate not found'}), 404

        existing = items[0]
        existing['is_active'] = False
        existing['updated_at'] = datetime.utcnow().isoformat()
        container.upsert_item(existing)
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tax_rates_blueprint.route('/invoices/calculate-tax', methods=['POST'])
def calculate_invoice_tax():
    """
    Calculate CGST/SGST/IGST for invoice items.

    Body:
    {
        "items":            [...],
        "customer_id":      "<id>",
        "place_of_supply":  "Maharashtra",  // optional override
        "is_gst_applicable": true
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        items = data.get('items', [])
        customer_id = data.get('customer_id')
        place_of_supply_override = (data.get('place_of_supply') or '').strip()
        is_gst_applicable = bool(data.get('is_gst_applicable', True))

        # Fetch seller state from org profile
        seller_state = _get_seller_state(request.tenant_id)

        # Fetch customer state & treatment
        if customer_id:
            customer_state, gst_treatment, customer_pos = _get_customer_state(
                request.tenant_id, str(customer_id)
            )
        else:
            customer_state, gst_treatment, customer_pos = '', 'regular', ''

        place_of_supply = place_of_supply_override or customer_pos or customer_state

        result = calculate_gst(
            items=items,
            seller_state=seller_state,
            customer_state=customer_state,
            gst_treatment=gst_treatment,
            is_gst_applicable=is_gst_applicable,
            place_of_supply=place_of_supply,
        )

        return jsonify({
            'success': True,
            'seller_state': seller_state,
            'customer_state': customer_state,
            'place_of_supply': place_of_supply,
            'gst_treatment': gst_treatment,
            **result,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
