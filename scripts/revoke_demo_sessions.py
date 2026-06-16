#!/usr/bin/env python3
"""Revoke refresh tokens for all users in the demo tenant (after nightly reset)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from azure.cosmos import CosmosClient  # noqa: E402

DEFAULT_DEMO_TENANT_ID = "d3m00000-0000-4000-8000-000000000001"


def main() -> None:
    tenant_id = os.getenv("DEMO_TENANT_ID", DEFAULT_DEMO_TENANT_ID).strip()
    uri = os.environ["COSMOS_URI"]
    key = os.environ["COSMOS_KEY"]
    db_name = os.getenv("COSMOS_DB_NAME", "smartinvoicedb")

    client = CosmosClient(uri, credential=key)
    database = client.get_database_client(db_name)
    users_ctr = database.get_container_client("users")
    tokens_ctr = database.get_container_client("refresh_tokens")

    demo_users = list(
        users_ctr.query_items(
            query="SELECT c.id, c.userid FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        )
    )

    deleted = 0
    for user in demo_users:
        uid = user.get("userid") or user.get("id")
        if not uid:
            continue
        token_items = list(
            tokens_ctr.query_items(
                query="SELECT c.id FROM c WHERE c.user_id = @uid",
                parameters=[{"name": "@uid", "value": uid}],
                partition_key=uid,
            )
        )
        for item in token_items:
            tokens_ctr.delete_item(item=item["id"], partition_key=uid)
            deleted += 1

    print(f"Revoked {deleted} refresh token(s) for demo tenant {tenant_id}")


if __name__ == "__main__":
    main()
