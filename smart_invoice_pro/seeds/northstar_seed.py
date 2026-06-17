"""
Curated NorthStar Industrial Supplies dataset for the public Interactive Workspace.

Generates a realistic operating B2B distribution business with full workflow chains.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import datetime, timedelta

from smart_invoice_pro.seeds.northstar_data import (
    CREDIT_LIMITS,
    CUSTOMERS,
    NORTHSTAR_INDUSTRY,
    NORTHSTAR_ORG_NAME,
    PAYMENT_TERMS,
    PRODUCTS,
    VENDORS,
)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _days_ago(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).date().isoformat()


def _gst_totals(line_items, interstate: bool):
    subtotal = sum(i["quantity"] * i["rate"] - i.get("discount", 0) for i in line_items)
    tax = sum(
        round((i["quantity"] * i["rate"] - i.get("discount", 0)) * i["tax"] / 100, 2)
        for i in line_items
    )
    if interstate:
        return subtotal, 0.0, 0.0, tax, tax
    half = round(tax / 2, 2)
    return subtotal, half, half, 0.0, tax


def _lookup_demo_user_id(tenant_id: str, role: str = "Manager") -> str:
    from smart_invoice_pro.utils.cosmos_client import users_container

    username = f"demo-{role.lower()}"
    rows = list(
        users_container.query_items(
            query=(
                "SELECT c.id FROM c WHERE c.tenant_id = @tid "
                "AND (c.username = @u OR c.email = @e)"
            ),
            parameters=[
                {"name": "@tid", "value": tenant_id},
                {"name": "@u", "value": username},
                {"name": "@e", "value": f"{username}@demo.internal"},
            ],
            enable_cross_partition_query=True,
        )
    )
    return rows[0]["id"] if rows else str(uuid.uuid4())


def _generate_gstin(state_code: str, seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest().upper()
    pan = digest[:10]
    return f"{state_code}{pan[:5]}{digest[5:9]}{digest[9]}{digest[10]}Z{digest[11]}"


def _state_code(state: str) -> str:
    codes = {
        "Maharashtra": "27",
        "Gujarat": "24",
        "Karnataka": "29",
        "Tamil Nadu": "33",
        "West Bengal": "19",
        "Rajasthan": "08",
        "Kerala": "32",
        "Punjab": "03",
        "Delhi": "07",
        "Telangana": "36",
        "Uttar Pradesh": "09",
        "Madhya Pradesh": "23",
        "Odisha": "21",
        "Bihar": "10",
    }
    return codes.get(state, "27")


def run_northstar_seed(tenant_id: str) -> None:
    """Seed NorthStar curated business data into Cosmos for the demo tenant."""
    random.seed(42)

    from smart_invoice_pro.utils.cosmos_client import (
        get_container,
        settings_container,
    )
    from seed_data import (
        HOME_STATE,
        PAYMENT_MODES,
        bank_import_batches_container,
        bank_import_jobs_container,
        bank_import_rows_container,
        bills_container,
        customers_container,
        expenses_container,
        invoices_container,
        payments_container,
        products_container,
        purchase_orders_container,
        quotes_container,
        sales_orders_container,
        seed_stock_initial,
        stock_container,
        vendors_container,
    )

    bank_accounts_container = get_container("bank_accounts", "/user_id")
    bank_txns_container = get_container("bank_transactions", "/user_id")

    manager_user_id = _lookup_demo_user_id(tenant_id, "Manager")
    accountant_user_id = _lookup_demo_user_id(tenant_id, "Accountant")
    now = _now_iso()

    print(f"\n=== NorthStar curated seed for tenant {tenant_id} ===\n")

    # ── Organization profile ─────────────────────────────────────────────
    profile_id = f"{tenant_id}:organization_profile"
    profile = {
        "id": profile_id,
        "type": "organization_profile",
        "tenant_id": tenant_id,
        "organization_name": NORTHSTAR_ORG_NAME,
        "legal_name": NORTHSTAR_ORG_NAME,
        "industry": NORTHSTAR_INDUSTRY,
        "country": "India",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "gst_mode": "regular",
        "gst_registration_type": "regular",
        "address": {
            "line1": "Plot 14, MIDC Industrial Area",
            "city": "Pune",
            "state": HOME_STATE,
            "pincode": "411057",
            "phone": "+91-20-4521-8800",
        },
        "created_at": now,
        "updated_at": now,
    }
    settings_container.upsert_item(body=profile)
    print(f"  Organization profile: {NORTHSTAR_ORG_NAME}")

    # ── Products ───────────────────────────────────────────────────────
    products = []
    for name, category, unit, price, reorder, tax, hsn in PRODUCTS:
        pid = str(uuid.uuid4())
        doc = {
            "id": pid,
            "product_id": pid,
            "tenant_id": tenant_id,
            "name": name,
            "category": category,
            "unit": unit,
            "price": float(price),
            "purchase_rate": round(price * 0.72, 2),
            "tax_rate": float(tax),
            "hsn_sac": hsn,
            "reorder_level": int(reorder),
            "reorder_qty": int(reorder * 2),
            "sales_enabled": True,
            "purchase_enabled": True,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
        }
        products_container.create_item(body=doc)
        products.append(doc)
    print(f"  Products: {len(products)}")
    stock_summary = seed_stock_initial(tenant_id, products)
    print(f"  Stock levels: {stock_summary}")

    # ── Vendors ────────────────────────────────────────────────────────
    vendors = []
    for idx, (name, state) in enumerate(VENDORS):
        vid = str(uuid.uuid4())
        slug = name.split()[0].lower()
        doc = {
            "id": vid,
            "vendor_id": vid,
            "tenant_id": tenant_id,
            "name": name,
            "contact_person": "Procurement Desk",
            "email": f"accounts@{slug}.in",
            "phone": f"98{70000000 + idx}",
            "state": state,
            "country": "India",
            "gst_number": _generate_gstin(_state_code(state), f"vendor-{name}"),
            "payment_terms": PAYMENT_TERMS[idx % len(PAYMENT_TERMS)],
            "status": "Active",
            "created_at": now,
            "updated_at": now,
        }
        vendors_container.create_item(body=doc)
        vendors.append(doc)
    print(f"  Vendors: {len(vendors)}")

    # ── Customers ──────────────────────────────────────────────────────
    customers = []
    for idx, (company, state, interstate) in enumerate(CUSTOMERS):
        cid = str(uuid.uuid4())
        slug = company.split()[0].lower()
        doc = {
            "id": cid,
            "customer_id": cid,
            "tenant_id": tenant_id,
            "display_name": company,
            "company_name": company,
            "email": f"finance@{slug}.com",
            "phone": f"99{80000000 + idx}",
            "customer_type": "Business",
            "gst_number": _generate_gstin(_state_code(state), f"customer-{company}"),
            "place_of_supply": state,
            "billing_street": f"Plot {idx + 1}, Industrial Estate",
            "billing_city": state.split()[0] if " " in state else "City",
            "billing_state": state,
            "billing_zip": f"{411000 + idx}",
            "billing_country": "India",
            "currency": "INR",
            "payment_terms": PAYMENT_TERMS[idx % len(PAYMENT_TERMS)],
            "credit_limit": float(CREDIT_LIMITS[idx % len(CREDIT_LIMITS)]),
            "contact_persons": [
                {
                    "name": f"Accounts {idx + 1}",
                    "email": f"ap@{slug}.com",
                    "phone": f"98{90000000 + idx}",
                    "is_primary": True,
                }
            ],
            "is_interstate": interstate,
            "created_at": _days_ago(30 + idx * 3),
            "updated_at": now,
        }
        customers_container.create_item(body=doc)
        customers.append(doc)
    print(f"  Customers: {len(customers)}")

    def _lines(prod_indices, qtys):
        items = []
        for idx, qty in zip(prod_indices, qtys):
            p = products[idx]
            base = qty * p["price"]
            tax_amt = round(base * p["tax_rate"] / 100, 2)
            items.append({
                "name": p["name"],
                "product_id": p["product_id"],
                "quantity": qty,
                "rate": p["price"],
                "discount": 0,
                "tax": p["tax_rate"],
                "amount": round(base + tax_amt, 2),
            })
        return items

    horizon = customers[5]
    apex = customers[6]
    prime_vendor = vendors[0]
    zenith_vendor = vendors[1]

    # ── Quotes (mixed statuses) ─────────────────────────────────────────
    quote_statuses = [
        ("Accepted", 18, 2),
        ("Sent", 8, 14),
        ("Draft", 3, 30),
        ("Declined", 25, 5),
        ("Expired", 40, 10),
    ]
    quote_count = 0
    for i, (status, issued_days, expiry_offset) in enumerate(quote_statuses):
        cust = customers[i % len(customers)]
        lines = _lines([i % len(products), (i + 3) % len(products)], [20 + i * 5, 50 + i * 10])
        sub, cgst, sgst, igst, total_tax = _gst_totals(lines, cust.get("is_interstate", False))
        quote_id = str(uuid.uuid4())
        quotes_container.create_item(body={
            "id": quote_id,
            "tenant_id": tenant_id,
            "customer_id": cust["customer_id"],
            "customer_name": cust["display_name"],
            "quote_number": f"QT-2026-{140 + i:04d}",
            "issue_date": _days_ago(issued_days),
            "expiry_date": _days_ago(issued_days - expiry_offset),
            "status": status,
            "subtotal": sub,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
            "total_tax": total_tax,
            "total_amount": round(sub + total_tax, 2),
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        quote_count += 1

    for i in range(5):
        cust = customers[(i + 10) % len(customers)]
        lines = _lines([(i + 5) % len(products)], [15 + i * 8])
        sub, cgst, sgst, igst, total_tax = _gst_totals(lines, cust.get("is_interstate", False))
        quotes_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "customer_id": cust["customer_id"],
            "customer_name": cust["display_name"],
            "quote_number": f"QT-2026-{150 + i:04d}",
            "issue_date": _days_ago(5 + i),
            "expiry_date": _days_ago(-20 + i),
            "status": random.choice(["Sent", "Draft", "Accepted"]),
            "subtotal": sub,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
            "total_tax": total_tax,
            "total_amount": round(sub + total_tax, 2),
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        quote_count += 1
    print(f"  Quotes: {quote_count}")

    # ── Flagship Quote → SO → Invoice chain (Horizon) ───────────────────
    quote_lines = _lines([0, 2], [200, 500])
    sub, cgst, sgst, igst, total_tax = _gst_totals(quote_lines, False)
    quote_id = str(uuid.uuid4())
    quotes_container.create_item(body={
        "id": quote_id,
        "tenant_id": tenant_id,
        "customer_id": horizon["customer_id"],
        "customer_name": horizon["display_name"],
        "quote_number": "QT-2026-0142",
        "issue_date": _days_ago(18),
        "expiry_date": _days_ago(2),
        "status": "Accepted",
        "subtotal": sub,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "igst_amount": igst,
        "total_tax": total_tax,
        "total_amount": round(sub + total_tax, 2),
        "items": quote_lines,
        "created_at": now,
        "updated_at": now,
    })
    quote_count += 1

    so_id = str(uuid.uuid4())
    sales_orders_container.create_item(body={
        "id": so_id,
        "tenant_id": tenant_id,
        "customer_id": horizon["customer_id"],
        "customer_name": horizon["display_name"],
        "so_number": "SO-2026-0088",
        "quote_id": quote_id,
        "issue_date": _days_ago(12),
        "status": "Confirmed",
        "subtotal": sub,
        "total_tax": total_tax,
        "total_amount": round(sub + total_tax, 2),
        "items": quote_lines,
        "created_at": now,
        "updated_at": now,
    })

    inv_paid_id = str(uuid.uuid4())
    paid_total = round(sub + total_tax, 2)
    invoices_container.create_item(body={
        "id": inv_paid_id,
        "tenant_id": tenant_id,
        "customer_id": horizon["customer_id"],
        "customer_name": horizon["display_name"],
        "invoice_number": "INV-2026-0315",
        "sales_order_id": so_id,
        "issue_date": _days_ago(8),
        "due_date": _days_ago(-22),
        "status": "Paid",
        "subtotal": sub,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "igst_amount": igst,
        "total_tax": total_tax,
        "total_amount": paid_total,
        "amount_paid": paid_total,
        "balance_due": 0.0,
        "payment_mode": "Bank Transfer",
        "items": quote_lines,
        "created_at": now,
        "updated_at": now,
    })

    # ── Additional invoices (status mix + dashboard volume) ─────────────
    invoice_ids_for_payment: list[tuple[str, float, str]] = [
        (inv_paid_id, paid_total, "completed"),
    ]

    overdue_lines = _lines([3, 5], [2, 80])
    sub_o, cgst_o, sgst_o, igst_o, tax_o = _gst_totals(overdue_lines, True)
    inv_overdue_id = str(uuid.uuid4())
    overdue_total = round(sub_o + tax_o, 2)
    invoices_container.create_item(body={
        "id": inv_overdue_id,
        "tenant_id": tenant_id,
        "customer_id": apex["customer_id"],
        "customer_name": apex["display_name"],
        "invoice_number": "INV-2026-0298",
        "issue_date": _days_ago(55),
        "due_date": _days_ago(10),
        "status": "Overdue",
        "subtotal": sub_o,
        "cgst_amount": cgst_o,
        "sgst_amount": sgst_o,
        "igst_amount": igst_o,
        "total_tax": tax_o,
        "total_amount": overdue_total,
        "amount_paid": 0.0,
        "balance_due": overdue_total,
        "items": overdue_lines,
        "created_at": now,
        "updated_at": now,
    })

    partial_lines = _lines([1, 6], [300, 25])
    sub_p, cgst_p, sgst_p, igst_p, tax_p = _gst_totals(partial_lines, False)
    partial_total = round(sub_p + tax_p, 2)
    partial_paid = round(partial_total * 0.45, 2)
    inv_partial_id = str(uuid.uuid4())
    invoices_container.create_item(body={
        "id": inv_partial_id,
        "tenant_id": tenant_id,
        "customer_id": customers[7]["customer_id"],
        "customer_name": customers[7]["display_name"],
        "invoice_number": "INV-2026-0331",
        "issue_date": _days_ago(25),
        "due_date": _days_ago(5),
        "status": "Partially Paid",
        "subtotal": sub_p,
        "cgst_amount": cgst_p,
        "sgst_amount": sgst_p,
        "igst_amount": igst_p,
        "total_tax": tax_p,
        "total_amount": partial_total,
        "amount_paid": partial_paid,
        "balance_due": round(partial_total - partial_paid, 2),
        "items": partial_lines,
        "created_at": now,
        "updated_at": now,
    })
    invoice_ids_for_payment.append((inv_partial_id, partial_paid, "completed"))

    draft_lines = _lines([7], [40])
    sub_d, cgst_d, sgst_d, igst_d, tax_d = _gst_totals(draft_lines, False)
    draft_total = round(sub_d + tax_d, 2)
    invoices_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "customer_id": customers[8]["customer_id"],
        "customer_name": customers[8]["display_name"],
        "invoice_number": "INV-2026-0344",
        "issue_date": _days_ago(2),
        "due_date": _days_ago(-28),
        "status": "Draft",
        "subtotal": sub_d,
        "cgst_amount": cgst_d,
        "sgst_amount": sgst_d,
        "igst_amount": igst_d,
        "total_tax": tax_d,
        "total_amount": draft_total,
        "amount_paid": 0.0,
        "balance_due": draft_total,
        "items": draft_lines,
        "created_at": now,
        "updated_at": now,
    })

    invoice_count = 4
    status_cycle = ["Issued", "Issued", "Paid", "Overdue", "Partially Paid", "Issued"]
    for i, cust in enumerate(customers):
        if i < 9:
            continue
        lines = _lines([i % len(products)], [10 + (i % 20)])
        sub_i, cgst_i, sgst_i, igst_i, tax_i = _gst_totals(
            lines, cust.get("is_interstate", False)
        )
        total_i = round(sub_i + tax_i, 2)
        status = status_cycle[i % len(status_cycle)]
        days_ago = 5 + (i % 60)
        amount_paid = total_i if status == "Paid" else (
            round(total_i * 0.4, 2) if status == "Partially Paid" else 0.0
        )
        inv_id = str(uuid.uuid4())
        invoices_container.create_item(body={
            "id": inv_id,
            "tenant_id": tenant_id,
            "customer_id": cust["customer_id"],
            "customer_name": cust["display_name"],
            "invoice_number": f"INV-2026-{400 + i:04d}",
            "issue_date": _days_ago(days_ago),
            "due_date": _days_ago(days_ago - 30),
            "status": status,
            "subtotal": sub_i,
            "cgst_amount": cgst_i,
            "sgst_amount": sgst_i,
            "igst_amount": igst_i,
            "total_tax": tax_i,
            "total_amount": total_i,
            "amount_paid": amount_paid,
            "balance_due": round(total_i - amount_paid, 2),
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        invoice_count += 1
        if status in ("Paid", "Partially Paid") and amount_paid > 0:
            invoice_ids_for_payment.append((inv_id, amount_paid, "completed"))

    for i in range(8):
        cust = customers[i % len(customers)]
        lines = _lines([(i + 2) % len(products), (i + 4) % len(products)], [12, 8])
        sub_i, cgst_i, sgst_i, igst_i, tax_i = _gst_totals(
            lines, cust.get("is_interstate", False)
        )
        total_i = round(sub_i + tax_i, 2)
        inv_id = str(uuid.uuid4())
        invoices_container.create_item(body={
            "id": inv_id,
            "tenant_id": tenant_id,
            "customer_id": cust["customer_id"],
            "customer_name": cust["display_name"],
            "invoice_number": f"INV-2026-{500 + i:04d}",
            "issue_date": _days_ago(3 + i * 2),
            "due_date": _days_ago(-25 + i),
            "status": "Issued",
            "subtotal": sub_i,
            "cgst_amount": cgst_i,
            "sgst_amount": sgst_i,
            "igst_amount": igst_i,
            "total_tax": tax_i,
            "total_amount": total_i,
            "amount_paid": 0.0,
            "balance_due": total_i,
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        invoice_count += 1

    print(f"  Invoices: {invoice_count}")

    # ── Purchase orders ────────────────────────────────────────────────
    po_id = str(uuid.uuid4())
    po_lines = _lines([2, 4], [1000, 50])
    sub_po, cgst_po, sgst_po, igst_po, tax_po = _gst_totals(po_lines, False)
    po_total = round(sub_po + tax_po, 2)
    purchase_orders_container.create_item(body={
        "id": po_id,
        "tenant_id": tenant_id,
        "vendor_id": prime_vendor["vendor_id"],
        "vendor_name": prime_vendor["name"],
        "po_number": "PO-2026-0045",
        "order_date": _days_ago(20),
        "delivery_date": _days_ago(5),
        "status": "Sent",
        "subtotal": sub_po,
        "total_tax": tax_po,
        "total_amount": po_total,
        "items": po_lines,
        "created_at": now,
        "updated_at": now,
    })
    po_count = 1

    for i in range(6):
        vendor = vendors[(i + 2) % len(vendors)]
        lines = _lines([(i + 1) % len(products)], [100 + i * 20])
        sub_po, _, _, _, tax_po = _gst_totals(lines, False)
        total_po = round(sub_po + tax_po, 2)
        purchase_orders_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "vendor_id": vendor["vendor_id"],
            "vendor_name": vendor["name"],
            "po_number": f"PO-2026-{50 + i:04d}",
            "order_date": _days_ago(15 + i * 4),
            "delivery_date": _days_ago(2 + i),
            "status": random.choice(["Draft", "Sent", "Confirmed", "Received"]),
            "subtotal": sub_po,
            "total_tax": tax_po,
            "total_amount": total_po,
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        po_count += 1
    print(f"  Purchase orders: {po_count}")

    # ── Bills (correct schema: payment_status, bill_date) ────────────────
    bills_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "vendor_id": prime_vendor["vendor_id"],
        "vendor_name": prime_vendor["name"],
        "bill_number": "BILL-2026-0189",
        "converted_from_po_id": po_id,
        "bill_date": _days_ago(15),
        "due_date": _days_ago(0),
        "payment_status": "Unpaid",
        "subtotal": sub_po,
        "tax_amount": tax_po,
        "total_amount": po_total,
        "amount_paid": 0.0,
        "balance_due": po_total,
        "items": po_lines,
        "created_at": now,
        "updated_at": now,
    })

    bills_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "vendor_id": zenith_vendor["vendor_id"],
        "vendor_name": zenith_vendor["name"],
        "bill_number": "BILL-2026-0201",
        "bill_date": _days_ago(30),
        "due_date": _days_ago(5),
        "payment_status": "Paid",
        "subtotal": 45000.0,
        "tax_amount": 8100.0,
        "total_amount": 53100.0,
        "amount_paid": 53100.0,
        "balance_due": 0.0,
        "items": _lines([2], [400]),
        "created_at": now,
        "updated_at": now,
    })
    bill_count = 2

    bill_statuses = ["Unpaid", "Paid", "Partially Paid", "Overdue", "Unpaid"]
    for i, vendor in enumerate(vendors[2:]):
        lines = _lines([(i + 3) % len(products)], [80 + i * 15])
        sub_b, _, _, _, tax_b = _gst_totals(lines, True)
        total_b = round(sub_b + tax_b, 2)
        status = bill_statuses[i % len(bill_statuses)]
        paid = total_b if status == "Paid" else (
            round(total_b * 0.5, 2) if status == "Partially Paid" else 0.0
        )
        bills_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "vendor_id": vendor["vendor_id"],
            "vendor_name": vendor["name"],
            "bill_number": f"BILL-2026-{210 + i:04d}",
            "bill_date": _days_ago(10 + i * 3),
            "due_date": _days_ago(-5 + i),
            "payment_status": status,
            "subtotal": sub_b,
            "tax_amount": tax_b,
            "total_amount": total_b,
            "amount_paid": paid,
            "balance_due": round(total_b - paid, 2),
            "items": lines,
            "created_at": now,
            "updated_at": now,
        })
        bill_count += 1
    print(f"  Bills: {bill_count}")

    # ── Customer payments ──────────────────────────────────────────────
    payment_count = 0
    for inv_id, amount, status in invoice_ids_for_payment[:18]:
        payments_container.create_item(body={
            "id": str(uuid.uuid4()),
            "user_id": manager_user_id,
            "tenant_id": tenant_id,
            "invoice_id": inv_id,
            "amount": amount,
            "currency": "INR",
            "status": status,
            "payment_mode": random.choice(PAYMENT_MODES),
            "reference": f"UTR-NSTAR-{88000 + payment_count}",
            "created_at": now,
            "updated_at": now,
        })
        payment_count += 1
    print(f"  Payments: {payment_count}")

    # ── Bank accounts ──────────────────────────────────────────────────
    operating_account_id = str(uuid.uuid4())
    payroll_account_id = str(uuid.uuid4())
    bank_accounts_container.create_item(body={
        "id": operating_account_id,
        "user_id": manager_user_id,
        "tenant_id": tenant_id,
        "account_name": "NorthStar Operating Account",
        "bank_name": "HDFC Bank",
        "account_number": "50200012345678",
        "ifsc": "HDFC0001234",
        "account_type": "Current",
        "currency": "INR",
        "opening_balance": 2450000.0,
        "current_balance": 3125800.0,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })
    bank_accounts_container.create_item(body={
        "id": payroll_account_id,
        "user_id": accountant_user_id,
        "tenant_id": tenant_id,
        "account_name": "NorthStar Payroll Account",
        "bank_name": "ICICI Bank",
        "account_number": "10200567890123",
        "ifsc": "ICIC0001020",
        "account_type": "Current",
        "currency": "INR",
        "opening_balance": 850000.0,
        "current_balance": 620000.0,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })
    print("  Bank accounts: 2")

    # ── Bank import batch + rows (reconciliation demo) ─────────────────
    batch_id = str(uuid.uuid4())
    bank_import_batches_container.create_item(body={
        "id": batch_id,
        "tenant_id": tenant_id,
        "user_id": accountant_user_id,
        "bank_account_id": operating_account_id,
        "filename": "northstar-hdfc-jan2026.csv",
        "status": "review_ready",
        "review_status": "review_required",
        "row_count": 0,
        "warning_count": 0,
        "warnings": [],
        "created_at": now,
        "updated_at": now,
        "completed_at": now,
    })
    bank_import_jobs_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": accountant_user_id,
        "batch_id": batch_id,
        "status": "completed",
        "stage": "review_ready",
        "progress": 100,
        "created_at": now,
        "updated_at": now,
        "completed_at": now,
        "error": None,
    })

    txn_descriptions = [
        ("NEFT CR METRO ENGINEERING", 185400.0),
        ("IMPS CR HORIZON MFG", 92450.0),
        ("UPI DR OFFICE RENT", -85000.0),
        ("NEFT DR PRIME IND COMPONENTS", -53100.0),
        ("CHQ DEP APEX ENGINEERING", 142800.0),
        ("ATM WDL CASH PETTY", -10000.0),
        ("NEFT CR VERTEX MANUFACTURING", 67800.0),
        ("BANK CHARGES JAN", -590.0),
    ]
    row_count = 0
    for i in range(40):
        desc, base_amt = txn_descriptions[i % len(txn_descriptions)]
        amount = round(base_amt * (0.85 + (i % 5) * 0.05), 2)
        if i % 7 == 0:
            amount = -abs(amount)
        txn_date = _days_ago(2 + i)
        fingerprint = hashlib.sha256(
            f"{tenant_id}|{operating_account_id}|{txn_date}|{amount}|{desc}".encode()
        ).hexdigest()
        bank_import_rows_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "user_id": accountant_user_id,
            "batch_id": batch_id,
            "bank_account_id": operating_account_id,
            "source_filename": "northstar-hdfc-jan2026.csv",
            "row_index": i,
            "normalized_date": txn_date,
            "description": f"{desc} #{i + 1}",
            "amount": amount,
            "currency": "INR",
            "direction": "credit" if amount > 0 else "debit",
            "confidence_score": 0.92 if i % 3 else 0.78,
            "confidence_level": "high" if i % 3 else "medium",
            "warnings": [],
            "review_status": "ready" if i % 3 == 0 else "pending_review",
            "fingerprint": fingerprint,
            "created_at": now,
            "updated_at": now,
        })
        row_count += 1

    batch_doc = bank_import_batches_container.read_item(
        item=batch_id, partition_key=tenant_id
    )
    batch_doc["row_count"] = row_count
    bank_import_batches_container.replace_item(item=batch_id, body=batch_doc)
    print(f"  Bank import rows: {row_count}")

    # Direct bank transactions (deposits / withdrawals)
    for i, (desc, amt) in enumerate(txn_descriptions):
        bank_txns_container.create_item(body={
            "id": str(uuid.uuid4()),
            "user_id": accountant_user_id,
            "tenant_id": tenant_id,
            "bank_account_id": operating_account_id,
            "date": _days_ago(5 + i),
            "description": desc,
            "amount": amt,
            "currency": "INR",
            "source": "manual",
            "match_status": "matched" if i < 4 else "unmatched",
            "created_at": now,
            "updated_at": now,
        })
    print(f"  Bank transactions: {len(txn_descriptions)}")

    # ── Expenses ───────────────────────────────────────────────────────
    expense_categories = [
        ("Travel", 4200),
        ("Utilities", 8900),
        ("Software Subscriptions", 12500),
        ("Rent", 85000),
        ("Fuel & Transport", 6800),
        ("Office Supplies", 3200),
        ("Insurance", 24000),
        ("Professional Fees", 15000),
    ]
    for i, (cat, amt) in enumerate(expense_categories):
        expenses_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "category": cat,
            "amount": float(amt),
            "expense_date": _days_ago(5 + i * 4),
            "payment_mode": PAYMENT_MODES[i % len(PAYMENT_MODES)],
            "vendor_name": vendors[i % len(vendors)]["name"],
            "description": f"{cat} — NorthStar operations",
            "status": "Approved",
            "created_at": now,
            "updated_at": now,
        })
    print(f"  Expenses: {len(expense_categories)}")

    print("\n=== NorthStar seed complete ===\n")
