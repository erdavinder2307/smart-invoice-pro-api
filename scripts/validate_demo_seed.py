#!/usr/bin/env python3
"""
Post-seed validation for the Interactive Workspace demo tenant.

Exits non-zero if container counts or referential integrity checks fail.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from azure.cosmos import CosmosClient  # noqa: E402

DEFAULT_DEMO_TENANT_ID = "d3m00000-0000-4000-8000-000000000001"
NORTHSTAR_ORG_NAME = "NorthStar Industrial Supplies Pvt Ltd"

THRESHOLDS = {
    "current": {
        "products": 8,
        "stock": 8,
        "customers": 5,
        "vendors": 4,
        "quotes": 1,
        "sales_orders": 1,
        "invoices": 5,
        "purchase_orders": 1,
        "bills": 2,
        "payments": 1,
        "bank_accounts": 1,
        "expenses": 4,
    },
    "expanded": {
        "products": 25,
        "stock": 25,
        "customers": 25,
        "vendors": 15,
        "quotes": 8,
        "sales_orders": 1,
        "invoices": 25,
        "purchase_orders": 6,
        "bills": 15,
        "payments": 10,
        "bank_accounts": 2,
        "bank_import_rows": 30,
        "expenses": 8,
    },
}

CONTAINER_PK = {
    "products": "/product_id",
    "stock": "/product_id",
    "customers": "/customer_id",
    "vendors": "/vendor_id",
    "quotes": "/customer_id",
    "sales_orders": "/customer_id",
    "invoices": "/customer_id",
    "purchase_orders": "/vendor_id",
    "bills": "/vendor_id",
    "payments": "/user_id",
    "bank_accounts": "/user_id",
    "bank_import_rows": "/tenant_id",
    "expenses": "/id",
}


def _connect():
    uri = os.environ["COSMOS_URI"]
    key = os.environ["COSMOS_KEY"]
    db_name = os.getenv("COSMOS_DB_NAME", "smartinvoicedb")
    client = CosmosClient(uri, credential=key)
    return client.get_database_client(db_name)


def _count(db, container: str, tenant_id: str) -> int:
    ctr = db.get_container_client(container)
    rows = list(
        ctr.query_items(
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    return int(rows[0]) if rows else 0


def _fetch_ids(db, container: str, tenant_id: str, field: str) -> set[str]:
    ctr = db.get_container_client(container)
    rows = list(
        ctr.query_items(
            query=f"SELECT c.{field} AS id FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    return {str(r["id"]) for r in rows if r.get("id")}


def validate(tenant_id: str, profile: str) -> list[str]:
    errors: list[str] = []
    mins = THRESHOLDS.get(profile, THRESHOLDS["expanded"])
    db = _connect()

    print(f"\n=== Demo seed validation (profile={profile}) ===")
    print(f"Tenant: {tenant_id}\n")

    counts = {}
    for container, minimum in mins.items():
        count = _count(db, container, tenant_id)
        counts[container] = count
        status = "OK" if count >= minimum else "FAIL"
        print(f"  [{status}] {container:20} {count:4}  (min {minimum})")
        if count < minimum:
            errors.append(f"{container}: count {count} < minimum {minimum}")

    # Org profile
    settings = db.get_container_client("settings")
    profile_id = f"{tenant_id}:organization_profile"
    try:
        org = settings.read_item(item=profile_id, partition_key=tenant_id)
        org_name = org.get("organization_name", "")
        if org_name != NORTHSTAR_ORG_NAME:
            errors.append(f"organization_name mismatch: {org_name!r}")
        else:
            print(f"  [OK] organization_name     {org_name}")
    except Exception as exc:
        errors.append(f"organization_profile missing: {exc}")

    # Referential integrity (R2)
    customer_ids = _fetch_ids(db, "customers", tenant_id, "customer_id")
    product_ids = _fetch_ids(db, "products", tenant_id, "product_id")
    vendor_ids = _fetch_ids(db, "vendors", tenant_id, "vendor_id")

    inv_ctr = db.get_container_client("invoices")
    invoices = list(
        inv_ctr.query_items(
            query="SELECT c.customer_id, c.status, c.amount_paid FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    for inv in invoices:
        cid = inv.get("customer_id")
        if cid and str(cid) not in customer_ids:
            errors.append(f"invoice references missing customer_id {cid}")
            break

    paid_invoices = [i for i in invoices if i.get("status") == "Paid"]
    if counts.get("invoices", 0) >= mins.get("invoices", 1) and not paid_invoices:
        errors.append("no Paid invoices found for dashboard revenue demo")

    po_ctr = db.get_container_client("purchase_orders")
    pos = list(
        po_ctr.query_items(
            query="SELECT c.vendor_id FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    for po in pos:
        vid = po.get("vendor_id")
        if vid and str(vid) not in vendor_ids:
            errors.append(f"purchase_order references missing vendor_id {vid}")
            break

    if counts.get("products", 0) and not product_ids:
        errors.append("products container empty after count check")

    # Workflow chain presence (R4)
    so_ctr = db.get_container_client("sales_orders")
    sos = list(
        so_ctr.query_items(
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid AND IS_DEFINED(c.quote_id)",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    if sos and int(sos[0]) < 1:
        errors.append("no sales_order linked to quote_id")

    pay_ctr = db.get_container_client("payments")
    pays = list(
        pay_ctr.query_items(
            query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid AND IS_DEFINED(c.invoice_id)",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )
    if pays and int(pays[0]) < 1:
        errors.append("no payment linked to invoice_id")

    print()
    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("VALIDATION PASSED")
    print()
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate demo tenant seed data")
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("DEMO_TENANT_ID", DEFAULT_DEMO_TENANT_ID),
    )
    parser.add_argument("--scenario", default="northstar", choices=["northstar"])
    parser.add_argument(
        "--min-profile",
        default="expanded",
        choices=["current", "expanded"],
        help="Minimum count profile (expanded matches Phase 2 NorthStar seed)",
    )
    args = parser.parse_args()

    if not os.getenv("COSMOS_URI") or not os.getenv("COSMOS_KEY"):
        print("ERROR: COSMOS_URI and COSMOS_KEY required", file=sys.stderr)
        sys.exit(1)

    errors = validate(args.tenant_id.strip(), args.min_profile)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
