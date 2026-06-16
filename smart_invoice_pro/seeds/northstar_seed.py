"""
Curated NorthStar Industrial Supplies dataset for the public Interactive Workspace.

No Faker names — fixed B2B distribution business with interconnected workflows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

NORTHSTAR_ORG_NAME = "NorthStar Industrial Supplies Pvt Ltd"
NORTHSTAR_INDUSTRY = "B2B Industrial Distribution"

CUSTOMERS = [
    ("Horizon Manufacturing Ltd", "Maharashtra", "27", False),
    ("Apex Engineering Solutions", "Karnataka", "29", True),
    ("Sterling Packaging Industries", "Gujarat", "24", True),
    ("Metro Retail Distribution", "Maharashtra", "27", False),
    ("Delta Infrastructure Services", "Tamil Nadu", "33", True),
]

VENDORS = [
    ("Prime Industrial Components", "Maharashtra"),
    ("Zenith Packaging Materials", "Gujarat"),
    ("Bharat Logistics Services", "Maharashtra"),
    ("Allied Electrical Traders", "Karnataka"),
]

PRODUCTS = [
    ("Industrial Safety Gloves", "Safety Equipment", "Nos", 450, 120, 18, "6116"),
    ("Stainless Fasteners M8", "Hardware", "Nos", 85, 500, 18, "7318"),
    ("Packaging Cartons 18x12", "Packaging", "Nos", 120, 800, 12, "4819"),
    ("Electrical Control Panels", "Electrical", "Nos", 18500, 15, 18, "8537"),
    ("PVC Insulation Tape", "Electrical", "Nos", 35, 200, 18, "3919"),
    ("Hydraulic Hose Assembly", "Industrial", "Nos", 2200, 40, 18, "4009"),
    ("Warehouse Labels Roll", "Packaging", "Roll", 280, 150, 12, "4821"),
    ("LED Flood Light 50W", "Electrical", "Nos", 1650, 60, 18, "9405"),
]


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


def run_northstar_seed(tenant_id: str) -> None:
    """Seed NorthStar curated business data into Cosmos for the demo tenant."""
    from smart_invoice_pro.utils.cosmos_client import settings_container
    from seed_data import (
        customers_container,
        products_container,
        invoices_container,
        vendors_container,
        bills_container,
        expenses_container,
        stock_container,
        quotes_container,
        sales_orders_container,
        purchase_orders_container,
        payments_container,
        bank_accounts_container,
        seed_stock_initial,
        PAYMENT_MODES,
        HOME_STATE,
    )

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
    print("  Organization profile: NorthStar Industrial Supplies Pvt Ltd")

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
            "reorder_level": float(reorder),
            "reorder_qty": float(reorder * 2),
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
    for name, state in VENDORS:
        vid = str(uuid.uuid4())
        doc = {
            "id": vid,
            "vendor_id": vid,
            "tenant_id": tenant_id,
            "name": name,
            "contact_person": "Procurement Desk",
            "email": f"accounts@{name.split()[0].lower()}.in",
            "phone": "9876543210",
            "state": state,
            "country": "India",
            "payment_terms": "Net 30",
            "status": "Active",
            "created_at": now,
            "updated_at": now,
        }
        vendors_container.create_item(body=doc)
        vendors.append(doc)
    print(f"  Vendors: {len(vendors)}")

    # ── Customers ──────────────────────────────────────────────────────
    customers = []
    for company, state, _code, interstate in CUSTOMERS:
        cid = str(uuid.uuid4())
        doc = {
            "id": cid,
            "customer_id": cid,
            "tenant_id": tenant_id,
            "display_name": company,
            "company_name": company,
            "email": f"finance@{company.split()[0].lower()}.com",
            "phone": "9898989898",
            "customer_type": "Business",
            "place_of_supply": state,
            "billing_state": state,
            "billing_country": "India",
            "currency": "INR",
            "payment_terms": "Net 30",
            "is_interstate": interstate,
            "created_at": _days_ago(45),
            "updated_at": now,
        }
        customers_container.create_item(body=doc)
        customers.append(doc)
    print(f"  Customers: {len(customers)}")

    horizon = customers[0]
    apex = customers[1]
    prime_vendor = vendors[0]
    zenith_vendor = vendors[1]

    # Helper to build line items
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

    # ── Quote → SO → Invoice chain (Horizon) ───────────────────────────
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

    # Overdue invoice (Apex)
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

    # Partial payment invoice
    partial_lines = _lines([1, 6], [300, 25])
    sub_p, cgst_p, sgst_p, igst_p, tax_p = _gst_totals(partial_lines, False)
    partial_total = round(sub_p + tax_p, 2)
    partial_paid = round(partial_total * 0.45, 2)
    inv_partial_id = str(uuid.uuid4())
    invoices_container.create_item(body={
        "id": inv_partial_id,
        "tenant_id": tenant_id,
        "customer_id": customers[2]["customer_id"],
        "customer_name": customers[2]["display_name"],
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

    # Draft invoice
    draft_lines = _lines([7], [40])
    sub_d, cgst_d, sgst_d, igst_d, tax_d = _gst_totals(draft_lines, False)
    invoices_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "customer_id": customers[3]["customer_id"],
        "customer_name": customers[3]["display_name"],
        "invoice_number": "INV-2026-0344",
        "issue_date": _days_ago(2),
        "due_date": _days_ago(-28),
        "status": "Draft",
        "subtotal": sub_d,
        "cgst_amount": cgst_d,
        "sgst_amount": sgst_d,
        "igst_amount": igst_d,
        "total_tax": tax_d,
        "total_amount": round(sub_d + tax_d, 2),
        "amount_paid": 0.0,
        "balance_due": round(sub_d + tax_d, 2),
        "items": draft_lines,
        "created_at": now,
        "updated_at": now,
    })

    # Issued (open) invoices for dashboard volume
    for i, cust in enumerate(customers):
        lines = _lines([i % len(products)], [10 + i * 5])
        sub_i, cgst_i, sgst_i, igst_i, tax_i = _gst_totals(
            lines, cust.get("is_interstate", False)
        )
        total_i = round(sub_i + tax_i, 2)
        invoices_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "customer_id": cust["customer_id"],
            "customer_name": cust["display_name"],
            "invoice_number": f"INV-2026-{320 + i:04d}",
            "issue_date": _days_ago(14 + i * 3),
            "due_date": _days_ago(-15 + i),
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

    print("  Invoices: workflow chain + varied statuses")

    # ── PO → Bill (Prime vendor) ───────────────────────────────────────
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
        "issue_date": _days_ago(20),
        "status": "Sent",
        "subtotal": sub_po,
        "total_tax": tax_po,
        "total_amount": po_total,
        "items": po_lines,
        "created_at": now,
        "updated_at": now,
    })

    bills_container.create_item(body={
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "vendor_id": prime_vendor["vendor_id"],
        "vendor_name": prime_vendor["name"],
        "bill_number": "BILL-2026-0189",
        "purchase_order_id": po_id,
        "issue_date": _days_ago(15),
        "due_date": _days_ago(0),
        "status": "Unpaid",
        "subtotal": sub_po,
        "total_tax": tax_po,
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
        "issue_date": _days_ago(30),
        "due_date": _days_ago(5),
        "status": "Paid",
        "subtotal": 45000.0,
        "total_tax": 8100.0,
        "total_amount": 53100.0,
        "amount_paid": 53100.0,
        "balance_due": 0.0,
        "items": _lines([2], [400]),
        "created_at": now,
        "updated_at": now,
    })
    print("  Purchase orders and bills")

    # ── Payment transaction (paid invoice) ─────────────────────────────
    demo_user_id = str(uuid.uuid4())  # placeholder partition key
    payments_container.create_item(body={
        "id": str(uuid.uuid4()),
        "user_id": demo_user_id,
        "tenant_id": tenant_id,
        "invoice_id": inv_paid_id,
        "amount": paid_total,
        "currency": "INR",
        "status": "completed",
        "payment_mode": "Bank Transfer",
        "reference": "UTR-NSTAR-88421",
        "created_at": now,
        "updated_at": now,
    })

    # ── Bank account ───────────────────────────────────────────────────
    bank_accounts_container.create_item(body={
        "id": str(uuid.uuid4()),
        "user_id": demo_user_id,
        "tenant_id": tenant_id,
        "account_name": "NorthStar Operating Account",
        "bank_name": "HDFC Bank",
        "account_number": "50200012345678",
        "ifsc": "HDFC0001234",
        "account_type": "Current",
        "currency": "INR",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    })
    print("  Payments and bank account")

    # ── Expenses ───────────────────────────────────────────────────────
    for i, (cat, amt) in enumerate([
        ("Travel", 4200),
        ("Utilities", 8900),
        ("Software Subscriptions", 12500),
        ("Rent", 85000),
    ]):
        expenses_container.create_item(body={
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "category": cat,
            "amount": float(amt),
            "expense_date": _days_ago(10 + i * 7),
            "payment_mode": PAYMENT_MODES[i % len(PAYMENT_MODES)],
            "vendor_name": vendors[i % len(vendors)]["name"],
            "description": f"{cat} — NorthStar operations",
            "status": "Approved",
            "created_at": now,
            "updated_at": now,
        })
    print("  Expenses: 4")

    print("\n=== NorthStar seed complete ===\n")
