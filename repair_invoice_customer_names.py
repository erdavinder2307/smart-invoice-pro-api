#!/usr/bin/env python3

import argparse
from typing import Optional

from smart_invoice_pro.utils.cosmos_client import customers_container, invoices_container


FALLBACK_LABEL = "Unknown (Customer Deleted)"


def is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def customer_display_name(customer: dict) -> str:
    display_name = str(customer.get("display_name") or "").strip()
    if display_name:
        return display_name

    company_name = str(customer.get("company_name") or "").strip()
    if company_name:
        return company_name

    full_name = " ".join(
        part for part in [customer.get("first_name"), customer.get("last_name")] if str(part or "").strip()
    ).strip()
    if full_name:
        return full_name

    return ""


def find_customer(customer_ref: str, tenant_id: Optional[str]) -> Optional[dict]:
    queries = [
        (
            "SELECT * FROM c WHERE c.id = @customer_ref"
            + (" AND c.tenant_id = @tenant_id" if tenant_id else ""),
            [
                {"name": "@customer_ref", "value": customer_ref},
                *([{"name": "@tenant_id", "value": tenant_id}] if tenant_id else []),
            ],
        ),
        (
            "SELECT * FROM c WHERE c.customer_id = @customer_ref"
            + (" AND c.tenant_id = @tenant_id" if tenant_id else ""),
            [
                {"name": "@customer_ref", "value": customer_ref},
                *([{"name": "@tenant_id", "value": tenant_id}] if tenant_id else []),
            ],
        ),
    ]

    for query, parameters in queries:
        customers = list(
            customers_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )
        if customers:
            return customers[0]
    return None


def iter_candidate_invoices(tenant_id: Optional[str], invoice_number: Optional[str]):
    query = "SELECT * FROM c WHERE IS_DEFINED(c.customer_id)"
    parameters = []
    if tenant_id:
        query += " AND c.tenant_id = @tenant_id"
        parameters.append({"name": "@tenant_id", "value": tenant_id})
    if invoice_number:
        query += " AND c.invoice_number = @invoice_number"
        parameters.append({"name": "@invoice_number", "value": invoice_number})

    invoices = list(
        invoices_container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True,
        )
    )
    return [invoice for invoice in invoices if not is_blank(invoice.get("customer_id")) and is_blank(invoice.get("customer_name"))]


def repair_invoice_customer_names(tenant_id: Optional[str], invoice_number: Optional[str], apply: bool) -> int:
    invoices = iter_candidate_invoices(tenant_id, invoice_number)
    print(f"Found {len(invoices)} invoice(s) with blank customer_name.")

    updated = 0
    for invoice in invoices:
        customer_ref = str(invoice.get("customer_id")).strip()
        scoped_tenant_id = tenant_id or invoice.get("tenant_id")
        customer = find_customer(customer_ref, scoped_tenant_id)
        resolved_name = customer_display_name(customer) if customer else ""
        next_name = resolved_name or FALLBACK_LABEL

        print(
            f"- {invoice.get('invoice_number', invoice.get('id'))}: customer_id={customer_ref} -> {next_name}"
            + (" [apply]" if apply else " [dry-run]")
        )

        if not apply:
            continue

        invoice["customer_name"] = next_name
        invoices_container.replace_item(item=invoice["id"], body=invoice)
        updated += 1

    print(f"Updated {updated} invoice(s).")
    return updated


def parse_args():
    parser = argparse.ArgumentParser(description="Repair invoices with blank customer_name values.")
    parser.add_argument("--tenant-id", help="Limit repair to a single tenant.")
    parser.add_argument("--invoice-number", help="Limit repair to a single invoice number.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script runs in dry-run mode.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    repair_invoice_customer_names(args.tenant_id, args.invoice_number, args.apply)