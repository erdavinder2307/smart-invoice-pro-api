"""
Roles & Approvals API
─────────────────────
Roles:  Admin | Manager | Sales | Accountant | Purchaser
Auth:   X-User-Id header (consistent with rest of the app)

Approval workflow
  Invoice:  Draft → [submit] → Pending Approval → [approve/reject] → Issued / Draft
  PO:       Draft → [submit] → Pending Approval → [approve/reject] → Sent    / Draft
"""
from flask import Blueprint, request, jsonify
from smart_invoice_pro.utils.cosmos_client import users_container, invoices_container, purchase_orders_container
from smart_invoice_pro.utils.audit_logger import log_audit
from datetime import datetime
from functools import wraps

roles_blueprint = Blueprint('roles', __name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VALID_ROLES = ['Admin', 'Manager', 'Sales', 'Accountant', 'Purchaser']
APPROVER_ROLES = {'Admin', 'Manager'}
DEFAULT_ROLE = 'Admin'   # first registered user becomes Admin by convention

# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_user_id():
    """Get user ID from JWT context (set by auth_middleware) or fallback to X-User-Id header."""
    # Prefer the JWT-decoded user_id attached by auth_middleware
    jwt_user_id = getattr(request, 'user_id', None)
    if jwt_user_id:
        return str(jwt_user_id).strip()
    # Fallback: legacy X-User-Id header (for backward compat)
    return request.headers.get('X-User-Id', '').strip()

def _fetch_user(user_id):
    """Return the user document from Cosmos or None."""
    if not user_id:
        return None
    query = f"SELECT * FROM c WHERE c.id = '{user_id}'"
    items = list(users_container.query_items(query=query, enable_cross_partition_query=True))
    return items[0] if items else None

def _get_role(user_id):
    """Return the role string for a user, defaulting to 'Sales'."""
    user = _fetch_user(user_id)
    if not user:
        return None
    return user.get('role', 'Sales')

def require_role(*allowed_roles):
    """Decorator: only allow requests from users with one of the specified roles.
    Works with both JWT-authenticated requests and legacy X-User-Id header.
    Returns 401 if unauthenticated, 403 if authenticated but wrong role.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = _get_user_id()
            if not uid:
                return jsonify({'error': 'Unauthorized — authentication required'}), 401
            role = _get_role(uid)
            if role is None:
                return jsonify({'error': 'Unauthorized — user not found'}), 401
            if role not in allowed_roles:
                return jsonify({'error': f'Forbidden. Required role: {", ".join(allowed_roles)}. Your role: {role}'}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── GET /api/my-role ──────────────────────────────────────────────────────────
@roles_blueprint.route('/my-role', methods=['GET'])
def get_my_role():
    """Return the current user's role."""
    uid = _get_user_id()
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    user = _fetch_user(uid)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({
        'user_id': uid,
        'username': user.get('username', ''),
        'role': user.get('role', 'Sales'),
    }), 200


# ── GET /api/users (Admin only) ───────────────────────────────────────────────
@roles_blueprint.route('/users', methods=['GET'])
@require_role('Admin')
def list_users():
    """List all users with their roles."""
    items = list(users_container.read_all_items())
    safe = [
        {
            'id': u.get('id'),
            'username': u.get('username', ''),
            'email': u.get('email', ''),
            'role': u.get('role', 'Sales'),
            'created_at': u.get('created_at', ''),
        }
        for u in items
        if u.get('type') != 'user_profile'   # skip profile documents
    ]
    return jsonify(safe), 200


# ── PUT /api/users/<user_id>/role (Admin only) ────────────────────────────────
@roles_blueprint.route('/users/<target_user_id>/role', methods=['PUT'])
@require_role('Admin')
def update_user_role(target_user_id):
    """Update a user's role."""
    data = request.get_json() or {}
    new_role = data.get('role', '').strip()
    if new_role not in VALID_ROLES:
        return jsonify({'error': f'Invalid role. Choose from: {", ".join(VALID_ROLES)}'}), 400

    user = _fetch_user(target_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Prevent removing the last Admin
    if user.get('role') == 'Admin' and new_role != 'Admin':
        admins = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.role = 'Admin'",
            enable_cross_partition_query=True
        ))
        if len(admins) <= 1:
            return jsonify({'error': 'Cannot remove the last Admin user'}), 400

    old_role = user.get('role')
    user['role'] = new_role
    user['updated_at'] = datetime.utcnow().isoformat()
    users_container.upsert_item(body=user)
    log_audit("user", "update", target_user_id,
              {"id": target_user_id, "role": old_role},
              {"id": target_user_id, "role": new_role},
              user_id=getattr(request, 'user_id', None),
              tenant_id=getattr(request, 'tenant_id', None))
    return jsonify({'id': target_user_id, 'role': new_role}), 200


# ── GET /api/approvals/pending ────────────────────────────────────────────────
@roles_blueprint.route('/approvals/pending', methods=['GET'])
def get_pending_approvals():
    """
    Return all invoices and POs with status 'Pending Approval'.
    Accessible by any authenticated user (display is filtered in the UI).
    """
    uid = _get_user_id()
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        inv_query = "SELECT * FROM c WHERE c.status = 'Pending Approval'"
        invoices = list(invoices_container.query_items(query=inv_query, enable_cross_partition_query=True))
    except Exception:
        invoices = []

    try:
        po_query = "SELECT * FROM c WHERE c.status = 'Pending Approval'"
        pos = list(purchase_orders_container.query_items(query=po_query, enable_cross_partition_query=True))
    except Exception:
        pos = []

    return jsonify({
        'invoices': [
            {
                'id': i.get('id'),
                'type': 'invoice',
                'invoice_number': i.get('invoice_number'),
                'customer_name': i.get('customer_name'),
                'total_amount': i.get('total_amount', 0),
                'submitted_by': i.get('submitted_by', ''),
                'submitted_at': i.get('submitted_at', ''),
                'status': i.get('status'),
            }
            for i in invoices
        ],
        'purchase_orders': [
            {
                'id': p.get('id'),
                'type': 'purchase_order',
                'po_number': p.get('po_number'),
                'vendor_name': p.get('vendor_name'),
                'total_amount': p.get('total_amount', 0),
                'submitted_by': p.get('submitted_by', ''),
                'submitted_at': p.get('submitted_at', ''),
                'status': p.get('status'),
            }
            for p in pos
        ],
        'total': len(invoices) + len(pos),
    }), 200


# ── Invoice approval workflow ─────────────────────────────────────────────────
def _get_invoice(invoice_id):
    query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
    items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
    return items[0] if items else None


@roles_blueprint.route('/invoices/<invoice_id>/submit-for-approval', methods=['POST'])
def submit_invoice_for_approval(invoice_id):
    """Submit a Draft invoice for manager approval."""
    uid = _get_user_id()
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401

    inv = _get_invoice(invoice_id)
    if not inv:
        return jsonify({'error': 'Invoice not found'}), 404
    if inv.get('status') not in ('Draft',):
        return jsonify({'error': f'Only Draft invoices can be submitted. Current status: {inv["status"]}'}), 400

    inv['status'] = 'Pending Approval'
    inv['submitted_by'] = uid
    inv['submitted_at'] = datetime.utcnow().isoformat()
    inv['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.upsert_item(body=inv)
    return jsonify({'id': invoice_id, 'status': 'Pending Approval'}), 200


@roles_blueprint.route('/invoices/<invoice_id>/approve', methods=['POST'])
@require_role('Admin', 'Manager', 'Accountant')
def approve_invoice(invoice_id):
    """Approve a pending invoice → status becomes Issued."""
    uid = _get_user_id()
    inv = _get_invoice(invoice_id)
    if not inv:
        return jsonify({'error': 'Invoice not found'}), 404
    if inv.get('status') != 'Pending Approval':
        return jsonify({'error': f'Invoice is not pending approval. Status: {inv["status"]}'}), 400

    inv['status'] = 'Issued'
    inv['approved_by'] = uid
    inv['approved_at'] = datetime.utcnow().isoformat()
    inv['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.upsert_item(body=inv)
    return jsonify({'id': invoice_id, 'status': 'Issued', 'approved_by': uid}), 200


@roles_blueprint.route('/invoices/<invoice_id>/reject', methods=['POST'])
@require_role('Admin', 'Manager', 'Accountant')
def reject_invoice(invoice_id):
    """Reject a pending invoice → status returns to Draft."""
    uid = _get_user_id()
    data = request.get_json() or {}
    inv = _get_invoice(invoice_id)
    if not inv:
        return jsonify({'error': 'Invoice not found'}), 404
    if inv.get('status') != 'Pending Approval':
        return jsonify({'error': f'Invoice is not pending approval. Status: {inv["status"]}'}), 400

    inv['status'] = 'Draft'
    inv['rejected_by'] = uid
    inv['rejected_at'] = datetime.utcnow().isoformat()
    inv['rejection_reason'] = data.get('reason', '')
    inv['updated_at'] = datetime.utcnow().isoformat()
    invoices_container.upsert_item(body=inv)
    return jsonify({'id': invoice_id, 'status': 'Draft'}), 200


# ── Purchase Order approval workflow ─────────────────────────────────────────
def _get_po(po_id):
    query = f"SELECT * FROM c WHERE c.id = '{po_id}'"
    items = list(purchase_orders_container.query_items(query=query, enable_cross_partition_query=True))
    return items[0] if items else None


@roles_blueprint.route('/purchase-orders/<po_id>/submit-for-approval', methods=['POST'])
def submit_po_for_approval(po_id):
    """Submit a Draft PO for manager approval."""
    uid = _get_user_id()
    if not uid:
        return jsonify({'error': 'Unauthorized'}), 401
    po = _get_po(po_id)
    if not po:
        return jsonify({'error': 'Purchase Order not found'}), 404
    if po.get('status') not in ('Draft',):
        return jsonify({'error': f'Only Draft POs can be submitted. Current status: {po["status"]}'}), 400

    po['status'] = 'Pending Approval'
    po['submitted_by'] = uid
    po['submitted_at'] = datetime.utcnow().isoformat()
    po['updated_at'] = datetime.utcnow().isoformat()
    purchase_orders_container.upsert_item(body=po)
    return jsonify({'id': po_id, 'status': 'Pending Approval'}), 200


@roles_blueprint.route('/purchase-orders/<po_id>/approve', methods=['POST'])
@require_role('Admin', 'Manager')
def approve_po(po_id):
    """Approve a pending PO → status becomes Sent."""
    uid = _get_user_id()
    po = _get_po(po_id)
    if not po:
        return jsonify({'error': 'Purchase Order not found'}), 404
    if po.get('status') != 'Pending Approval':
        return jsonify({'error': f'PO is not pending approval. Status: {po["status"]}'}), 400

    po['status'] = 'Sent'
    po['approved_by'] = uid
    po['approved_at'] = datetime.utcnow().isoformat()
    po['updated_at'] = datetime.utcnow().isoformat()
    purchase_orders_container.upsert_item(body=po)
    return jsonify({'id': po_id, 'status': 'Sent', 'approved_by': uid}), 200


@roles_blueprint.route('/purchase-orders/<po_id>/reject', methods=['POST'])
@require_role('Admin', 'Manager')
def reject_po(po_id):
    """Reject a pending PO → status returns to Draft."""
    uid = _get_user_id()
    data = request.get_json() or {}
    po = _get_po(po_id)
    if not po:
        return jsonify({'error': 'Purchase Order not found'}), 404
    if po.get('status') != 'Pending Approval':
        return jsonify({'error': f'PO is not pending approval. Status: {po["status"]}'}), 400

    po['status'] = 'Draft'
    po['rejected_by'] = uid
    po['rejected_at'] = datetime.utcnow().isoformat()
    po['rejection_reason'] = data.get('reason', '')
    po['updated_at'] = datetime.utcnow().isoformat()
    purchase_orders_container.upsert_item(body=po)
    return jsonify({'id': po_id, 'status': 'Draft'}), 200
