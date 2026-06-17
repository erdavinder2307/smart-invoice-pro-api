#!/usr/bin/env python3
"""
Provision the public Solidev Books demo tenant (isolated from production).

Creates:
  - tenants doc (is_demo=True)
  - organization_profile in settings
  - 5 system roles (via roles_permissions_api seeder)
  - 4 demo persona users (Sales, Manager, Accountant, Purchaser — no Admin)
  - optional seed_data.py balanced dataset

Usage:
    cd smart-invoice-pro-api-2
    source venv/bin/activate
    python scripts/provision_demo_environment.py
    python scripts/provision_demo_environment.py --seed
    python scripts/provision_demo_environment.py --seed --reset
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from azure.cosmos import CosmosClient  # noqa: E402

from smart_invoice_pro.api.roles_permissions_api import (  # noqa: E402
    _get_or_seed_roles,
    _get_role_by_name,
)
from smart_invoice_pro.utils.tenant_service import get_tenant_by_id  # noqa: E402

# Fixed demo tenant — never reuse production tenant IDs.
DEFAULT_DEMO_TENANT_ID = "d3m00000-0000-4000-8000-000000000001"

DEMO_ROLES = ["Sales", "Manager", "Accountant", "Purchaser"]
DEMO_ORG_NAME = "NorthStar Industrial Supplies Pvt Ltd"
DEMO_SCENARIO = "northstar"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _connect():
    uri = os.environ["COSMOS_URI"]
    key = os.environ["COSMOS_KEY"]
    db_name = os.getenv("COSMOS_DB_NAME", "smartinvoicedb")
    client = CosmosClient(uri, credential=key)
    database = client.get_database_client(db_name)
    return (
        database.get_container_client("tenants"),
        database.get_container_client("users"),
        database.get_container_client("settings"),
    )


def ensure_demo_tenant(tenants_ctr, tenant_id: str) -> dict:
    existing = get_tenant_by_id(tenant_id)
    if existing:
        existing["is_demo"] = True
        existing["tenant_type"] = "DEMO"
        existing["name"] = DEMO_ORG_NAME
        existing["plan"] = existing.get("plan") or "trial"
        existing["status"] = "active"
        existing["updated_at"] = _now_iso()
        tenants_ctr.upsert_item(body=existing)
        print(f"  Updated existing demo tenant: {tenant_id}")
        return existing

    now = _now_iso()
    doc = {
        "id": tenant_id,
        "name": DEMO_ORG_NAME,
        "status": "active",
        "plan": "trial",
        "is_demo": True,
        "tenant_type": "DEMO",
        "created_at": now,
        "updated_at": now,
    }
    tenants_ctr.create_item(body=doc)
    print(f"  Created demo tenant: {tenant_id}")
    return doc


def ensure_org_profile(settings_ctr, tenant_id: str) -> None:
    doc_id = f"{tenant_id}:organization_profile"
    try:
        settings_ctr.read_item(item=doc_id, partition_key=tenant_id)
        print("  Organization profile already exists")
        return
    except Exception:
        pass

    now = _now_iso()
    doc = {
        "id": doc_id,
        "type": "organization_profile",
        "tenant_id": tenant_id,
        "organization_name": DEMO_ORG_NAME,
        "legal_name": DEMO_ORG_NAME,
        "industry": "B2B Industrial Distribution",
        "country": "India",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "gst_mode": "regular",
        "created_at": now,
        "updated_at": now,
    }
    settings_ctr.create_item(body=doc)
    print("  Created organization profile")


def ensure_demo_users(users_ctr, tenant_id: str) -> dict[str, str]:
    """Return mapping role -> user_id."""
    _get_or_seed_roles(tenant_id)
    role_map: dict[str, str] = {}
    placeholder_pw = generate_password_hash(
        uuid.uuid4().hex, method="pbkdf2:sha256", salt_length=16
    )

    for role in DEMO_ROLES:
        role_doc = _get_role_by_name(role, tenant_id)
        role_id = role_doc["id"] if role_doc else None
        username = f"demo-{role.lower()}"
        email = f"demo-{role.lower()}@demo.internal"

        existing = list(
            users_ctr.query_items(
                query=(
                    "SELECT * FROM c WHERE c.tenant_id = @tid "
                    "AND (c.username = @username OR c.email = @email)"
                ),
                parameters=[
                    {"name": "@tid", "value": tenant_id},
                    {"name": "@username", "value": username},
                    {"name": "@email", "value": email},
                ],
                enable_cross_partition_query=True,
            )
        )

        now = _now_iso()
        if existing:
            user = existing[0]
            user["role"] = role
            user["role_id"] = role_id
            user["is_demo_user"] = True
            user["is_active"] = True
            user["updated_at"] = now
            users_ctr.upsert_item(body=user)
            user_id = user["id"]
            print(f"  Updated demo user [{role}]: {username}")
        else:
            user_id = str(uuid.uuid4())
            user = {
                "id": user_id,
                "userid": user_id,
                "tenant_id": tenant_id,
                "username": username,
                "email": email,
                "name": f"Demo {role}",
                "password": placeholder_pw,
                "role": role,
                "role_id": role_id,
                "is_demo_user": True,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            users_ctr.create_item(body=user)
            print(f"  Created demo user [{role}]: {username}")

        role_map[role] = user_id

    return role_map


def run_seed(tenant_id: str, reset: bool, yes: bool = False) -> int:
    seed_script = ROOT / "seed_data.py"
    if not seed_script.exists():
        print("  seed_data.py not found — skipping data seed")
        return 0

    cmd = [
        sys.executable,
        str(seed_script),
        f"--tenant_id={tenant_id}",
        f"--scenario={DEMO_SCENARIO}",
        "--seed=42",
    ]
    if reset:
        cmd.append("--reset")
    if yes:
        cmd.append("--yes")
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)

    if proc.returncode != 0:
        print(f"  ERROR: seed_data exited with code {proc.returncode}")
    else:
        print("  Seed data completed")
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Solidev Books demo tenant")
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("DEMO_TENANT_ID", DEFAULT_DEMO_TENANT_ID),
    )
    parser.add_argument("--seed", action="store_true", help="Run seed_data.py balanced scenario")
    parser.add_argument("--reset", action="store_true", help="Wipe tenant data before seeding")
    parser.add_argument("--yes", action="store_true", help="Skip interactive reset confirmation")
    args = parser.parse_args()

    tenant_id = args.tenant_id.strip()
    print(f"\n=== Provisioning demo tenant: {tenant_id} ===\n")

    tenants_ctr, users_ctr, settings_ctr = _connect()
    ensure_demo_tenant(tenants_ctr, tenant_id)
    ensure_org_profile(settings_ctr, tenant_id)
    ensure_demo_users(users_ctr, tenant_id)

    if args.seed:
        print("\n=== Seeding demo data ===\n")
        seed_rc = run_seed(tenant_id, reset=args.reset, yes=args.yes)
        if seed_rc != 0:
            sys.exit(seed_rc)

    print("\n=== Done ===")
    print(f"DEMO_TENANT_ID={tenant_id}")
    print("Set on App Service: DEMO_ENABLED=true")


if __name__ == "__main__":
    main()
