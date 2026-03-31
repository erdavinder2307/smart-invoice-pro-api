"""
Roles & Permissions API
=======================
Granular per-module / per-action permission system.

System roles (seeded per-tenant on first access):
    Admin | Manager | Sales | Accountant | Purchaser

Permission document schema (container: "roles", partition: /tenant_id):
{
    "id":             "<uuid>",
    "tenant_id":      "<tenant_id>",
    "name":           "Accountant",
    "is_system_role": true,
    "permissions": {
        "invoices":        {"view": true, "create": true, "edit": true,  "delete": false},
        "quotes":          {"view": true, "create": false,"edit": false, "delete": false},
        "customers":       {"view": true, "create": false,"edit": false, "delete": false},
        "products":        {"view": true, "create": false,"edit": false, "delete": false},
        "vendors":         {"view": true, "create": false,"edit": false, "delete": false},
        "purchase_orders": {"view": true, "create": false,"edit": false, "delete": false},
        "bills":           {"view": true, "create": true, "edit": true,  "delete": false},
        "expenses":        {"view": true, "create": true, "edit": true,  "delete": false},
        "reports":         {"view": true},
        "settings":        {"view": false,"edit": false}
    },
    "created_at": "...",
    "updated_at": "..."
}

Endpoints:
  GET    /api/settings/roles           – list roles for tenant
  POST   /api/settings/roles           – create custom role (Admin)
  PUT    /api/settings/roles/<id>      – update role (Admin)
  DELETE /api/settings/roles/<id>      – delete custom role only (Admin)

  GET    /api/settings/users           – list users in tenant (Admin)
  POST   /api/settings/users           – invite / create user (Admin)
  PUT    /api/settings/users/<id>      – update role/status (Admin)
  DELETE /api/settings/users/<id>      – deactivate user (Admin)

  GET    /api/settings/permissions     – current user's full permission map
"""

import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash

from smart_invoice_pro.utils.cosmos_client import users_container, get_container
from smart_invoice_pro.api.roles_api import require_role
from smart_invoice_pro.utils.audit_logger import log_audit
import copy

roles_permissions_blueprint = Blueprint('roles_permissions', __name__)

# ── Permission modules & actions ──────────────────────────────────────────────
PERMISSION_MODULES = {
    'invoices':        ['view', 'create', 'edit', 'delete'],
    'quotes':          ['view', 'create', 'edit', 'delete'],
    'customers':       ['view', 'create', 'edit', 'delete'],
    'products':        ['view', 'create', 'edit', 'delete'],
    'vendors':         ['view', 'create', 'edit', 'delete'],
    'purchase_orders': ['view', 'create', 'edit', 'delete'],
    'bills':           ['view', 'create', 'edit', 'delete'],
    'expenses':        ['view', 'create', 'edit', 'delete'],
    'reports':         ['view'],
    'settings':        ['view', 'edit'],
}

# ── Default permissions for each system role ──────────────────────────────────
def _all(actions): return {a: True for a in actions}
def _none(actions): return {a: False for a in actions}
def _some(actions, allowed): return {a: (a in allowed) for a in actions}

SYSTEM_ROLE_DEFAULTS = {
    'Admin': {m: _all(a) for m, a in PERMISSION_MODULES.items()},
    'Manager': {
        'invoices':        _all(PERMISSION_MODULES['invoices']),
        'quotes':          _all(PERMISSION_MODULES['quotes']),
        'customers':       _some(PERMISSION_MODULES['customers'],    ['view', 'create', 'edit']),
        'products':        _some(PERMISSION_MODULES['products'],     ['view', 'create', 'edit']),
        'vendors':         _some(PERMISSION_MODULES['vendors'],      ['view', 'create', 'edit']),
        'purchase_orders': _some(PERMISSION_MODULES['purchase_orders'], ['view', 'create', 'edit']),
        'bills':           _some(PERMISSION_MODULES['bills'],        ['view', 'create', 'edit']),
        'expenses':        _some(PERMISSION_MODULES['expenses'],     ['view', 'create', 'edit']),
        'reports':         _all(PERMISSION_MODULES['reports']),
        'settings':        _none(PERMISSION_MODULES['settings']),
    },
    'Sales': {
        'invoices':        _some(PERMISSION_MODULES['invoices'],     ['view', 'create', 'edit']),
        'quotes':          _some(PERMISSION_MODULES['quotes'],       ['view', 'create', 'edit']),
        'customers':       _some(PERMISSION_MODULES['customers'],    ['view', 'create', 'edit']),
        'products':        _some(PERMISSION_MODULES['products'],     ['view']),
        'vendors':         _none(PERMISSION_MODULES['vendors']),
        'purchase_orders': _none(PERMISSION_MODULES['purchase_orders']),
        'bills':           _none(PERMISSION_MODULES['bills']),
        'expenses':        _some(PERMISSION_MODULES['expenses'],     ['view', 'create', 'edit']),
        'reports':         _none(PERMISSION_MODULES['reports']),
        'settings':        _none(PERMISSION_MODULES['settings']),
    },
    'Accountant': {
        'invoices':        _some(PERMISSION_MODULES['invoices'],     ['view', 'create', 'edit']),
        'quotes':          _some(PERMISSION_MODULES['quotes'],       ['view']),
        'customers':       _some(PERMISSION_MODULES['customers'],    ['view']),
        'products':        _some(PERMISSION_MODULES['products'],     ['view']),
        'vendors':         _some(PERMISSION_MODULES['vendors'],      ['view']),
        'purchase_orders': _some(PERMISSION_MODULES['purchase_orders'], ['view']),
        'bills':           _some(PERMISSION_MODULES['bills'],        ['view', 'create', 'edit']),
        'expenses':        _some(PERMISSION_MODULES['expenses'],     ['view', 'create', 'edit']),
        'reports':         _all(PERMISSION_MODULES['reports']),
        'settings':        _none(PERMISSION_MODULES['settings']),
    },
    'Purchaser': {
        'invoices':        _some(PERMISSION_MODULES['invoices'],     ['view']),
        'quotes':          _none(PERMISSION_MODULES['quotes']),
        'customers':       _some(PERMISSION_MODULES['customers'],    ['view']),
        'products':        _some(PERMISSION_MODULES['products'],     ['view', 'create', 'edit']),
        'vendors':         _some(PERMISSION_MODULES['vendors'],      ['view', 'create', 'edit']),
        'purchase_orders': _some(PERMISSION_MODULES['purchase_orders'], ['view', 'create', 'edit']),
        'bills':           _some(PERMISSION_MODULES['bills'],        ['view', 'create', 'edit']),
        'expenses':        _some(PERMISSION_MODULES['expenses'],     ['view', 'create', 'edit']),
        'reports':         _none(PERMISSION_MODULES['reports']),
        'settings':        _none(PERMISSION_MODULES['settings']),
    },
}


def _get_roles_container():
    return get_container("roles", "/tenant_id")


# ── System role seeder ────────────────────────────────────────────────────────

def _seed_system_roles(tenant_id: str) -> list:
    """Insert the 5 system roles for a brand-new tenant. Returns the seeded docs."""
    container = _get_roles_container()
    now = datetime.utcnow().isoformat()
    seeded = []
    for role_name, perms in SYSTEM_ROLE_DEFAULTS.items():
        doc = {
            'id':             str(uuid.uuid4()),
            'tenant_id':      tenant_id,
            'name':           role_name,
            'is_system_role': True,
            'permissions':    perms,
            'created_at':     now,
            'updated_at':     now,
        }
        container.create_item(body=doc)
        seeded.append(doc)
    return seeded


def _get_or_seed_roles(tenant_id: str) -> list:
    """Return all role docs for tenant, seeding defaults if none exist."""
    container = _get_roles_container()
    roles = list(container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    if not roles:
        roles = _seed_system_roles(tenant_id)
    return roles


def _get_role_by_id(role_id: str, tenant_id: str):
    container = _get_roles_container()
    items = list(container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": role_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def _get_role_by_name(role_name: str, tenant_id: str):
    # Ensure roles are seeded first
    _get_or_seed_roles(tenant_id)
    container = _get_roles_container()
    items = list(container.query_items(
        query="SELECT * FROM c WHERE c.name = @name AND c.tenant_id = @tid",
        parameters=[
            {"name": "@name", "value": role_name},
            {"name": "@tid",  "value": tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


# ── Fetch a user by user_id (cross-partition) ─────────────────────────────────
def _fetch_user_by_id(user_id: str):
    items = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.id = @uid",
        parameters=[{"name": "@uid", "value": user_id}],
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


# ── Permission checker ────────────────────────────────────────────────────────
def _check_permission(user_id: str, tenant_id: str, module: str, action: str) -> bool:
    """
    Returns True if the user has permission for module+action.
    Admin always passes. Falls back to role name if no role_id set.
    """
    user = _fetch_user_by_id(user_id)
    if not user:
        return False

    # Admin shortcut (works even before role docs are seeded)
    if user.get('role') == 'Admin':
        return True

    # Try role_id first, then role name
    role_id = user.get('role_id')
    if role_id:
        role_doc = _get_role_by_id(role_id, tenant_id)
    else:
        role_name = user.get('role', 'Sales')
        role_doc = _get_role_by_name(role_name, tenant_id)

    if not role_doc:
        return False

    perms = role_doc.get('permissions', {})
    return bool(perms.get(module, {}).get(action, False))


# ── permission_required decorator ─────────────────────────────────────────────
def permission_required(module: str, action: str):
    """
    Decorator: enforces that the authenticated user has permission for
    permissions[module][action].  Admin always passes.

    Usage:
        @permission_required("invoices", "create")
        def create_invoice(): ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_id = getattr(request, 'user_id', None)
            tenant_id = getattr(request, 'tenant_id', None)
            if not user_id or not tenant_id:
                return jsonify({'error': 'Unauthorized'}), 401
            if not _check_permission(user_id, tenant_id, module, action):
                return jsonify({
                    'error': f'Forbidden: you do not have {action} permission on {module}.'
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ── GET /api/settings/permissions ────────────────────────────────────────────
@roles_permissions_blueprint.route('/settings/permissions', methods=['GET'])
def get_my_permissions():
    """Return the full permission map for the current authenticated user."""
    try:
        user_id = request.user_id
        tenant_id = request.tenant_id
        user = _fetch_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Admin → synthesise full permissions
        if user.get('role') == 'Admin':
            full_perms = {m: _all(a) for m, a in PERMISSION_MODULES.items()}
            return jsonify({
                'user_id':  user_id,
                'role':     'Admin',
                'is_admin': True,
                'permissions': full_perms,
            }), 200

        role_id = user.get('role_id')
        if role_id:
            role_doc = _get_role_by_id(role_id, tenant_id)
        else:
            role_doc = _get_role_by_name(user.get('role', 'Sales'), tenant_id)

        perms = role_doc.get('permissions', {}) if role_doc else {}
        return jsonify({
            'user_id':     user_id,
            'role':        user.get('role', 'Sales'),
            'role_id':     role_id,
            'is_admin':    False,
            'permissions': perms,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Roles CRUD ────────────────────────────────────────────────────────────────

@roles_permissions_blueprint.route('/settings/roles', methods=['GET'])
def list_roles():
    """List all roles for the current tenant (seeds defaults on first call)."""
    try:
        roles = _get_or_seed_roles(request.tenant_id)
        # Strip CosmosDB internal fields
        safe = [{k: v for k, v in r.items() if not k.startswith('_')} for r in roles]
        return jsonify(safe), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/roles', methods=['POST'])
@require_role('Admin')
def create_role():
    """Create a new custom role (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'name is required'}), 400

        # Ensure no duplicate name for this tenant
        existing = _get_role_by_name(name, request.tenant_id)
        if existing:
            return jsonify({'error': f'A role named "{name}" already exists'}), 409

        # Build permissions from supplied dict; default missing actions to False
        perms_in = data.get('permissions') or {}
        permissions = {}
        for mod, actions in PERMISSION_MODULES.items():
            permissions[mod] = {a: bool(perms_in.get(mod, {}).get(a, False)) for a in actions}

        now = datetime.utcnow().isoformat()
        doc = {
            'id':             str(uuid.uuid4()),
            'tenant_id':      request.tenant_id,
            'name':           name,
            'is_system_role': False,
            'permissions':    permissions,
            'created_at':     now,
            'updated_at':     now,
        }
        _get_roles_container().create_item(body=doc)
        return jsonify({k: v for k, v in doc.items() if not k.startswith('_')}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/roles/<role_id>', methods=['PUT'])
@require_role('Admin')
def update_role(role_id):
    """Update role name (custom) or permissions (all roles). Admin only."""
    try:
        role_doc = _get_role_by_id(role_id, request.tenant_id)
        if not role_doc:
            return jsonify({'error': 'Role not found'}), 404

        data = request.get_json(silent=True) or {}

        # System roles: only allow editing permissions, not name
        if not role_doc['is_system_role']:
            new_name = (data.get('name') or role_doc['name']).strip()
            if not new_name:
                return jsonify({'error': 'name is required'}), 400
            role_doc['name'] = new_name

        # Merge incoming permissions
        perms_in = data.get('permissions')
        if perms_in is not None:
            for mod, actions in PERMISSION_MODULES.items():
                if mod in perms_in:
                    existing_mod = role_doc['permissions'].get(mod, {})
                    role_doc['permissions'][mod] = {
                        a: bool(perms_in[mod].get(a, existing_mod.get(a, False)))
                        for a in actions
                    }

        role_doc['updated_at'] = datetime.utcnow().isoformat()
        _get_roles_container().upsert_item(role_doc)
        return jsonify({k: v for k, v in role_doc.items() if not k.startswith('_')}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/roles/<role_id>', methods=['DELETE'])
@require_role('Admin')
def delete_role(role_id):
    """Delete a custom role (Admin only). System roles cannot be deleted."""
    try:
        role_doc = _get_role_by_id(role_id, request.tenant_id)
        if not role_doc:
            return jsonify({'error': 'Role not found'}), 404
        if role_doc.get('is_system_role'):
            return jsonify({'error': 'System roles cannot be deleted'}), 400

        # Reassign users of this role to a fallback role
        users_on_role = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.role_id = @rid AND c.tenant_id = @tid",
            parameters=[
                {"name": "@rid", "value": role_id},
                {"name": "@tid", "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        fallback = _get_role_by_name('Sales', request.tenant_id)
        for u in users_on_role:
            u['role_id'] = fallback['id'] if fallback else None
            u['role'] = 'Sales'
            u['updated_at'] = datetime.utcnow().isoformat()
            users_container.upsert_item(u)

        _get_roles_container().delete_item(item=role_id, partition_key=request.tenant_id)
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Users CRUD (tenant scoped) ────────────────────────────────────────────────

def _safe_user(u: dict) -> dict:
    return {
        'id':         u.get('id'),
        'username':   u.get('username', ''),
        'email':      u.get('email', ''),
        'name':       u.get('name', u.get('username', '')),
        'role':       u.get('role', 'Sales'),
        'role_id':    u.get('role_id'),
        'is_active':  u.get('is_active', True),
        'created_at': u.get('created_at', ''),
    }


@roles_permissions_blueprint.route('/settings/users', methods=['GET'])
@require_role('Admin')
def list_settings_users():
    """List all users for the current tenant (Admin only)."""
    try:
        items = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": request.tenant_id}],
            enable_cross_partition_query=True,
        ))
        # Filter out profile docs
        users = [_safe_user(u) for u in items if u.get('type') != 'user_profile']
        return jsonify(users), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/users', methods=['POST'])
@require_role('Admin')
def invite_user():
    """
    Invite a new user to the tenant (Admin only).
    Creates account immediately with a temporary password.
    Body: { name, email, username, password, role_id?, role? }
    """
    try:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or '').strip().lower()
        username = (data.get('username') or email.split('@')[0]).strip()
        password = (data.get('password') or '').strip()
        name = (data.get('name') or username).strip()

        if not email or '@' not in email:
            return jsonify({'error': 'Valid email is required'}), 400
        if not username:
            return jsonify({'error': 'username is required'}), 400
        if not password or len(password) < 6:
            return jsonify({'error': 'password must be at least 6 characters'}), 400

        # Check duplicate email within tenant
        dupes = list(users_container.query_items(
            query="SELECT * FROM c WHERE c.email = @email AND c.tenant_id = @tid",
            parameters=[
                {"name": "@email", "value": email},
                {"name": "@tid",   "value": request.tenant_id},
            ],
            enable_cross_partition_query=True,
        ))
        if dupes:
            return jsonify({'error': 'A user with this email already exists'}), 409

        # Resolve role
        role_id = data.get('role_id')
        role_name = (data.get('role') or 'Sales').strip()
        if role_id:
            role_doc = _get_role_by_id(role_id, request.tenant_id)
            if role_doc:
                role_name = role_doc['name']
            else:
                role_id = None
        if not role_id:
            role_doc = _get_role_by_name(role_name, request.tenant_id)
            role_id = role_doc['id'] if role_doc else None

        user_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

        user_doc = {
            'id':         user_id,
            'userid':     user_id,           # partition key
            'tenant_id':  request.tenant_id,
            'name':       name,
            'username':   username,
            'email':      email,
            'password':   hashed_pw,
            'role':       role_name,
            'role_id':    role_id,
            'is_active':  True,
            'invited_by': request.user_id,
            'created_at': now,
            'updated_at': now,
        }
        users_container.create_item(body=user_doc)
        log_audit("user", "create", user_doc["id"], None, user_doc,
                  user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
        return jsonify(_safe_user(user_doc)), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/users/<target_user_id>', methods=['PUT'])
@require_role('Admin')
def update_settings_user(target_user_id):
    """Update a user's name, role, or active status (Admin only)."""
    try:
        user = _fetch_user_by_id(target_user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        if user.get('tenant_id') != request.tenant_id:
            return jsonify({'error': 'Forbidden'}), 403

        before_snapshot = copy.deepcopy(user)
        data = request.get_json(silent=True) or {}

        if 'name' in data:
            user['name'] = (data['name'] or '').strip()
        if 'email' in data:
            user['email'] = (data['email'] or '').strip().lower()

        # Role update
        role_id = data.get('role_id')
        role_name = data.get('role')
        if role_id:
            role_doc = _get_role_by_id(role_id, request.tenant_id)
            if not role_doc:
                return jsonify({'error': 'Role not found'}), 404
            user['role_id'] = role_id
            user['role'] = role_doc['name']
        elif role_name:
            role_doc = _get_role_by_name(role_name.strip(), request.tenant_id)
            user['role'] = role_name.strip()
            user['role_id'] = role_doc['id'] if role_doc else user.get('role_id')

        if 'is_active' in data:
            # Prevent deactivating the last Admin
            if user.get('role') == 'Admin' and not data['is_active']:
                admins = list(users_container.query_items(
                    query="SELECT * FROM c WHERE c.role = 'Admin' AND c.tenant_id = @tid AND c.is_active = true",
                    parameters=[{"name": "@tid", "value": request.tenant_id}],
                    enable_cross_partition_query=True,
                ))
                if len(admins) <= 1:
                    return jsonify({'error': 'Cannot deactivate the last active Admin'}), 400
            user['is_active'] = bool(data['is_active'])

        user['updated_at'] = datetime.utcnow().isoformat()
        users_container.upsert_item(user)
        log_audit("user", "update", target_user_id, before_snapshot, user,
                  user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
        return jsonify(_safe_user(user)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_permissions_blueprint.route('/settings/users/<target_user_id>', methods=['DELETE'])
@require_role('Admin')
def deactivate_user(target_user_id):
    """Deactivate (soft-delete) a user. Cannot deactivate yourself or last Admin."""
    try:
        if target_user_id == request.user_id:
            return jsonify({'error': 'You cannot deactivate your own account'}), 400

        user = _fetch_user_by_id(target_user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        if user.get('tenant_id') != request.tenant_id:
            return jsonify({'error': 'Forbidden'}), 403

        if user.get('role') == 'Admin':
            admins = list(users_container.query_items(
                query="SELECT * FROM c WHERE c.role = 'Admin' AND c.tenant_id = @tid AND c.is_active = true",
                parameters=[{"name": "@tid", "value": request.tenant_id}],
                enable_cross_partition_query=True,
            ))
            if len(admins) <= 1:
                return jsonify({'error': 'Cannot deactivate the last active Admin'}), 400

        before_snapshot = copy.deepcopy(user)
        user['is_active'] = False
        user['updated_at'] = datetime.utcnow().isoformat()
        users_container.upsert_item(user)
        log_audit("user", "delete", target_user_id, before_snapshot, user,
                  user_id=getattr(request, 'user_id', None), tenant_id=request.tenant_id)
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
