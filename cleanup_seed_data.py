"""
cleanup_seed_data.py — Remove all seeded data for a given tenant from Smart Invoice Pro.

Deletes every document whose tenant_id matches the supplied value from:
  customers, products, invoices, vendors, bills, expenses, stock

Usage:
    python cleanup_seed_data.py --tenant_id=<id> --confirm

The --confirm flag is required as a safety guard against accidental data loss.

Environment variables (loaded from .env):
    COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME
"""

import argparse
import os

from azure.cosmos import CosmosClient, PartitionKey
from dotenv import load_dotenv

load_dotenv()

COSMOS_URI     = os.getenv("COSMOS_URI")
COSMOS_KEY     = os.getenv("COSMOS_KEY")
COSMOS_DB_NAME = os.getenv("COSMOS_DB_NAME")

if not all([COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME]):
    raise SystemExit(
        "Missing required environment variables: COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME\n"
        "Ensure a .env file is present or the variables are exported in your shell."
    )

_client   = CosmosClient(COSMOS_URI, credential=COSMOS_KEY)
_database = _client.create_database_if_not_exists(id=COSMOS_DB_NAME)


def _get_container(name: str, partition_key: str):
    return _database.create_container_if_not_exists(
        id=name,
        partition_key=PartitionKey(path=partition_key),
    )


# (container_name, partition_path, partition_field_in_document)
CONTAINERS = [
    ("customers", "/customer_id", "customer_id"),
    ("products",  "/product_id",  "product_id"),
    ("invoices",  "/customer_id", "customer_id"),
    ("vendors",   "/vendor_id",   "vendor_id"),
    ("bills",     "/vendor_id",   "vendor_id"),
    ("expenses",  "/id",          "id"),
    ("stock",     "/product_id",  "product_id"),
]


def _delete_tenant_docs(tenant_id: str) -> dict:
    """
    Delete all documents belonging to `tenant_id` across every container.
    Returns a dict of {container_name: count_deleted}.
    """
    totals = {}

    for container_name, partition_path, partition_field in CONTAINERS:
        container = _get_container(container_name, partition_path)

        # SELECT only the id and partition-key field to keep the payload small
        docs = list(container.query_items(
            query=f"SELECT c.id, c.{partition_field} FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))

        count = 0
        for doc in docs:
            try:
                # Use the document's own partition-key value; fall back to id
                # (expenses use id as the partition key)
                pk_value = doc.get(partition_field) or doc.get("id")
                container.delete_item(item=doc["id"], partition_key=pk_value)
                count += 1
            except Exception as exc:
                print(f"  [WARN] Could not delete {container_name}/{doc['id']}: {exc}")

        totals[container_name] = count
        print(f"  {container_name:<12} : {count} document(s) deleted")

    return totals


def main():
    parser = argparse.ArgumentParser(
        description="Delete all data for a given tenant from Smart Invoice Pro.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tenant_id", required=True,
        help="Tenant ID whose data will be deleted",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Must be passed to actually perform the deletion (safety guard)",
    )
    args = parser.parse_args()

    if not args.confirm:
        print("ERROR: --confirm flag is required to proceed.")
        print(f"       This will permanently delete ALL data for tenant: {args.tenant_id}")
        print("       Re-run with --confirm to continue.")
        return

    print(f"\nDeleting all data for tenant: {args.tenant_id}")
    print("=" * 50)

    totals = _delete_tenant_docs(args.tenant_id)

    total_deleted = sum(totals.values())
    print("\n" + "=" * 50)
    print("  CLEANUP COMPLETE — SUMMARY")
    print("=" * 50)
    for name, count in totals.items():
        print(f"  {name:<14}: {count} deleted")
    print(f"  {'─' * 34}")
    print(f"  {'TOTAL':<14}: {total_deleted} documents deleted")
    print("=" * 50)


if __name__ == "__main__":
    main()
