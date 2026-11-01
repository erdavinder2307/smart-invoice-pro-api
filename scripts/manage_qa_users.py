"""
manage_qa_users.py
==================
Provision QA / testing accounts covering every user type in Solidev Books, so the
web app can be exercised end-to-end as each role.

Accounts created (all usernames prefixed `qa.` so they are easy to spot and purge):

    qa.admin       Admin           full access, bypasses granular checks
    qa.manager     Manager         ops role, no settings/billing
    qa.sales       Sales           quotes/invoices/customers only
    qa.accountant  Accountant      finance + billing.view
    qa.purchaser   Purchaser       procurement side
    qa.finance     Finance Manager finance + billing.manage
    qa.readonly    Read Only       view-only across every module
    qa.suspended   Sales           is_active=False + status=suspended (negative test)
    qa.portal      Customer        user_type=customer, portal workspace

Usage
-----
    cd smart-invoice-pro-api-2
    source venv/bin/activate

    python scripts/manage_qa_users.py tenants                  # discover tenant ids
    python scripts/manage_qa_users.py list    --tenant <id>
    python scripts/manage_qa_users.py create  --tenant <id> --dry-run
    python scripts/manage_qa_users.py create  --tenant <id>
    python scripts/manage_qa_users.py delete  --tenant <id> --yes

Password
--------
Read from the QA_USER_PASSWORD environment variable. If unset, a strong random
password is generated and printed ONCE at the end of the run. Never hardcoded.

    export QA_USER_PASSWORD='SomeStrongPassphrase'

Safety
------
This writes to whatever Cosmos instance the API .env points at. `create` and
`delete` both refuse to run without an explicit --tenant, and `delete` only ever
removes users whose username starts with the qa. prefix.
"""
from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from werkzeug.security import generate_password_hash  # noqa: E402

from smart_invoice_pro.utils.cosmos_client import (  # noqa: E402
    users_container,
    get_container,
)

QA_PREFIX = "qa."
EMAIL_DOMAIN = os.getenv("QA_EMAIL_DOMAIN", "qa.solidevbooks.local")

# (local_part, role_name, user_type, is_active, status)
QA_USERS = [
    ("admin",      "Admin",           "staff",    True,  "active"),
    ("manager",    "Manager",         "staff",    True,  "active"),
    ("sales",      "Sales",           "staff",    True,  "active"),
    ("accountant", "Accountant",      "staff",    True,  "active"),
    ("purchaser",  "Purchaser",       "staff",    True,  "active"),
    ("finance",    "Finance Manager", "staff",    True,  "active"),
    ("readonly",   "Read Only",       "staff",    True,  "active"),
    ("suspended",  "Sales",           "staff",    False, "suspended"),
    ("portal",     "Customer",        "customer", True,  "active"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _username(local: str) -> str:
    return f"{QA_PREFIX}{local}@{EMAIL_DOMAIN}"


def _gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Qa!" + "".join(secrets.choice(alphabet) for _ in range(16))


def _roles_for_tenant(tenant_id: str) -> dict:
    """Return {role_name: role_id} for the tenant, seeding system roles if absent."""
    from smart_invoice_pro.api.roles_permissions_api import _get_or_seed_roles

    return {r["name"]: r["id"] for r in _get_or_seed_roles(tenant_id)}


def _find_user(tenant_id: str, username: str) -> dict | None:
    rows = list(users_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id=@t AND c.username=@u",
        parameters=[{"name": "@t", "value": tenant_id},
                    {"name": "@u", "value": username}],
        enable_cross_partition_query=True,
    ))
    return rows[0] if rows else None


def _first_customer_id(tenant_id: str) -> str | None:
    """Portal users must link to a real customer; grab any one from the tenant."""
    try:
        rows = list(get_container("customers", "/tenant_id").query_items(
            query=("SELECT TOP 1 c.id FROM c WHERE c.tenant_id=@t "
                   "AND (NOT IS_DEFINED(c.is_deleted) OR c.is_deleted=false)"),
            parameters=[{"name": "@t", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        return rows[0]["id"] if rows else None
    except Exception:
        return None


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_sync_roles(args) -> int:
    """
    Backfill system roles that exist in SYSTEM_ROLE_DEFAULTS but not yet in the
    tenant's roles container.

    Why this is needed: `_get_or_seed_roles()` seeds a tenant only when it has
    ZERO roles. Any role added to SYSTEM_ROLE_DEFAULTS after a tenant was first
    created therefore never reaches that tenant, and a user assigned such a role
    resolves to no permissions at all.
    """
    import copy
    import uuid as _uuid
    from smart_invoice_pro.api.roles_permissions_api import (
        SYSTEM_ROLE_DEFAULTS, _get_or_seed_roles,
    )
    from smart_invoice_pro.utils.rbac_resolver import invalidate_tenant_permission_cache

    ctr = get_container("roles", "/tenant_id")
    existing = {r["name"]: r for r in _get_or_seed_roles(args.tenant)}

    missing = [n for n in SYSTEM_ROLE_DEFAULTS if n not in existing]
    stale = []
    for name, role_doc in existing.items():
        if name not in SYSTEM_ROLE_DEFAULTS:
            continue
        want = set(SYSTEM_ROLE_DEFAULTS[name].keys())
        have = set((role_doc.get("permissions") or {}).keys())
        if want - have:
            stale.append((name, sorted(want - have)))

    print(f"tenant {args.tenant}")
    print(f"  roles in code : {len(SYSTEM_ROLE_DEFAULTS)}")
    print(f"  roles in db   : {len(existing)}")
    print(f"  missing       : {missing or 'none'}")
    if stale:
        print("  missing permission modules on existing roles:")
        for name, mods in stale:
            print(f"    {name:<18} + {', '.join(mods)}")
    if not missing and not stale:
        print("\n  nothing to do — tenant is in sync.")
        return 0

    if args.dry_run:
        print("\n  (dry run — nothing written)")
        return 0

    now = _now()
    for name in missing:
        ctr.create_item(body={
            "id": str(_uuid.uuid4()),
            "tenant_id": args.tenant,
            "name": name,
            "is_system_role": True,
            "permissions": copy.deepcopy(SYSTEM_ROLE_DEFAULTS[name]),
            "created_at": now,
            "updated_at": now,
        })
        print(f"  + created role {name}")

    if args.update_existing:
        for name, mods in stale:
            doc = existing[name]
            perms = doc.get("permissions") or {}
            for m in mods:
                perms[m] = copy.deepcopy(SYSTEM_ROLE_DEFAULTS[name][m])
            doc["permissions"] = perms
            doc["updated_at"] = now
            ctr.replace_item(item=doc["id"], body=doc)
            print(f"  ~ updated {name}: added {', '.join(mods)}")
    elif stale:
        print("\n  (existing roles left untouched — pass --update-existing to backfill"
              " their missing permission modules)")

    invalidate_tenant_permission_cache(args.tenant)
    print("\n  permission cache invalidated for tenant.")
    return 0


def cmd_tenants(_args) -> int:
    rows = list(users_container.query_items(
        query="SELECT DISTINCT c.tenant_id FROM c WHERE IS_DEFINED(c.tenant_id)",
        enable_cross_partition_query=True,
    ))
    print(f"{len(rows)} tenant(s) with user records:\n")
    for r in rows:
        tid = r.get("tenant_id")
        n = list(users_container.query_items(
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id=@t",
            parameters=[{"name": "@t", "value": tid}],
            enable_cross_partition_query=True,
        ))
        print(f"  {tid}   ({n[0] if n else '?'} users)")
    return 0


def cmd_list(args) -> int:
    print(f"QA users in tenant {args.tenant}:\n")
    print(f"  {'USERNAME':<38}{'ROLE':<18}{'TYPE':<10}{'ACTIVE':<8}STATUS")
    print("  " + "-" * 82)
    found = 0
    for local, role, utype, _a, _s in QA_USERS:
        u = _find_user(args.tenant, _username(local))
        if not u:
            print(f"  {_username(local):<38}{'—':<18}{'—':<10}{'—':<8}NOT CREATED")
            continue
        found += 1
        print(f"  {u.get('username',''):<38}{u.get('role',''):<18}"
              f"{u.get('user_type','staff'):<10}{str(u.get('is_active','')):<8}"
              f"{u.get('status','active')}")
    print(f"\n  {found}/{len(QA_USERS)} QA users present.")
    return 0


def cmd_create(args) -> int:
    password = os.getenv("QA_USER_PASSWORD") or ""
    generated = False
    if not password:
        password = _gen_password()
        generated = True

    roles = _roles_for_tenant(args.tenant)
    missing = [r for _l, r, t, _a, _s in QA_USERS
               if t == "staff" and r not in roles]
    if missing:
        print(f"!! Roles not found in tenant: {sorted(set(missing))}")
        print("   Run:  python scripts/manage_qa_users.py sync-roles --tenant "
              f"{args.tenant}")
        print("   (system roles are only seeded when a tenant has zero roles, so"
              " roles added later never reach existing tenants)")
        return 1

    customer_id = _first_customer_id(args.tenant)
    pw_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    print(f"{'DRY RUN — ' if args.dry_run else ''}creating QA users in tenant {args.tenant}\n")
    created = updated = skipped = 0

    for local, role, utype, is_active, status in QA_USERS:
        username = _username(local)
        existing = _find_user(args.tenant, username)

        if utype == "customer" and not customer_id:
            print(f"  SKIP   {username:<38} no customer in tenant to link")
            skipped += 1
            continue

        doc = {
            "tenant_id":  args.tenant,
            "name":       f"QA {local.title()}",
            "username":   username,
            "email":      username,
            "password":   pw_hash,
            "role":       role,
            "role_id":    roles.get(role),
            "user_type":  utype,
            "is_active":  is_active,
            "status":     status,
            "is_qa_user": True,
            "updated_at": _now(),
        }
        if utype == "customer":
            doc["linked_customer_id"] = customer_id

        if existing:
            doc["id"] = existing["id"]
            doc["userid"] = existing.get("userid", existing["id"])
            doc["created_at"] = existing.get("created_at", _now())
            action, updated = "UPDATE", updated + 1
        else:
            import uuid
            uid = str(uuid.uuid4())
            doc["id"] = uid
            doc["userid"] = uid
            doc["created_at"] = _now()
            action, created = "CREATE", created + 1

        if not args.dry_run:
            users_container.upsert_item(body=doc)

        extra = f" -> customer {customer_id[:8]}" if utype == "customer" else ""
        print(f"  {action} {username:<38}{role:<18}{'(suspended)' if status=='suspended' else ''}{extra}")

    print(f"\n  created={created} updated={updated} skipped={skipped}")
    if args.dry_run:
        print("  (dry run — nothing written)")
        return 0

    print("\n  Sign in at /login with any username above.")
    if generated:
        print(f"\n  GENERATED PASSWORD (shown once): {password}")
        print("  Re-run with QA_USER_PASSWORD set to choose your own.")
    else:
        print("  Password: value of $QA_USER_PASSWORD")
    print("\n  Note: qa.suspended should be REJECTED at login — that is the expected result.")
    print("  Note: qa.portal signs in to the customer portal workspace, not the app.")
    return 0


def cmd_delete(args) -> int:
    victims = []
    for local, _r, _t, _a, _s in QA_USERS:
        u = _find_user(args.tenant, _username(local))
        if u:
            victims.append(u)

    if not victims:
        print("No QA users found — nothing to delete.")
        return 0

    print(f"Will delete {len(victims)} QA user(s) from tenant {args.tenant}:")
    for u in victims:
        print(f"  {u.get('username')}  (id={u.get('id')})")

    for u in victims:
        if not str(u.get("username", "")).startswith(QA_PREFIX):
            print(f"!! refusing to delete non-QA user {u.get('username')}")
            return 1

    if not args.yes:
        print("\nRe-run with --yes to confirm deletion.")
        return 0

    for u in victims:
        users_container.delete_item(item=u["id"], partition_key=u.get("userid", u["id"]))
        print(f"  deleted {u.get('username')}")
    print(f"\n{len(victims)} QA user(s) deleted.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Manage Solidev Books QA test users")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tenants", help="list tenant ids that have users")

    pl = sub.add_parser("list", help="show QA users for a tenant")
    pl.add_argument("--tenant", required=True)

    pc = sub.add_parser("create", help="create/update QA users")
    pc.add_argument("--tenant", required=True)
    pc.add_argument("--dry-run", action="store_true")

    ps = sub.add_parser("sync-roles", help="backfill missing system roles into a tenant")
    ps.add_argument("--tenant", required=True)
    ps.add_argument("--dry-run", action="store_true")
    ps.add_argument("--update-existing", action="store_true",
                    help="also add newly-introduced permission modules to existing role docs")

    pd = sub.add_parser("delete", help="remove QA users")
    pd.add_argument("--tenant", required=True)
    pd.add_argument("--yes", action="store_true", help="confirm deletion")

    args = p.parse_args()
    return {"tenants": cmd_tenants, "list": cmd_list, "sync-roles": cmd_sync_roles,
            "create": cmd_create, "delete": cmd_delete}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
