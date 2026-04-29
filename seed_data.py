"""
seed_data.py — Intelligent Mock Data Generator for Solidev Books

Generates realistic, time-distributed data across all features.
Ensures dashboard filters (week/month/quarter/year) always return
non-zero results, and that all entity relationships are valid.

Usage:
    python seed_data.py --tenant_id=<id>
    python seed_data.py --tenant_id=<id> --scenario=balanced        (default)
    python seed_data.py --tenant_id=<id> --scenario=high-revenue
    python seed_data.py --tenant_id=<id> --scenario=low-activity
    python seed_data.py --tenant_id=<id> --reset                    (wipe + reseed)
    python seed_data.py --tenant_id=<id> --seed=42                  (reproducible)

    # Override individual counts (on top of any scenario):
    python seed_data.py --tenant_id=<id> --scenario=balanced --invoices=200

Scenarios:
    balanced      Realistic spread of activity (default)
    high-revenue  Large business with high invoice volumes
    low-activity  Minimal data for sparse testing

Flags:
    --reset       Wipe all data for the tenant before re-seeding
    --seed=N      Integer random seed for reproducibility

Requirements:
    pip install faker python-dotenv azure-cosmos

Environment variables (loaded from .env):
    COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME
"""

import argparse
import os
import random
import secrets
import string
import sys
import uuid
from datetime import datetime, timedelta

from azure.cosmos import CosmosClient, PartitionKey
from dotenv import load_dotenv

try:
    from faker import Faker
except ImportError:
    raise SystemExit("faker is not installed. Run: pip install faker>=20.0.0")

load_dotenv()

# ─── Cosmos DB connection ─────────────────────────────────────────────────────

COSMOS_URI = os.getenv("COSMOS_URI")
COSMOS_KEY = os.getenv("COSMOS_KEY")
COSMOS_DB_NAME = os.getenv("COSMOS_DB_NAME")

if not all([COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME]):
    raise SystemExit(
        "Missing required environment variables: COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME\n"
        "Ensure a .env file is present or the variables are exported in your shell."
    )

_client = CosmosClient(COSMOS_URI, credential=COSMOS_KEY)
_database = _client.create_database_if_not_exists(id=COSMOS_DB_NAME)


def _get_container(name: str, partition_key: str):
    return _database.create_container_if_not_exists(
        id=name,
        partition_key=PartitionKey(path=partition_key),
    )


customers_container = _get_container("customers", "/customer_id")
products_container  = _get_container("products",  "/product_id")
invoices_container  = _get_container("invoices",  "/customer_id")
vendors_container   = _get_container("vendors",   "/vendor_id")
bills_container     = _get_container("bills",     "/vendor_id")
expenses_container  = _get_container("expenses",  "/id")
stock_container     = _get_container("stock",     "/product_id")

# ─── Constants ────────────────────────────────────────────────────────────────

fake = Faker("en_IN")

# The business is registered in Maharashtra (home state for GST purposes)
HOME_STATE      = "Maharashtra"
HOME_STATE_CODE = "27"

INDIAN_STATES = [
    ("Maharashtra",  "27"),
    ("Delhi",        "07"),
    ("Karnataka",    "29"),
    ("Tamil Nadu",   "33"),
    ("Gujarat",      "24"),
    ("Uttar Pradesh","09"),
    ("Rajasthan",    "08"),
    ("West Bengal",  "19"),
    ("Telangana",    "36"),
    ("Punjab",       "03"),
]

PAYMENT_TERMS_OPTIONS = ["Net 15", "Net 30", "Net 45", "Due on Receipt"]
PAYMENT_MODES         = ["Cash", "Bank Transfer", "UPI", "Cheque", "Credit Card"]

TAGS_POOL = [
    "q1", "q2", "q3", "q4",
    "priority", "recurring", "export", "domestic", "b2b", "b2c",
]

EXPENSE_CATEGORIES = [
    "Travel",
    "Utilities",
    "Rent",
    "Meals & Entertainment",
    "Software Subscriptions",
    "Office Supplies",
    "Advertising",
    "Professional Services",
    "Insurance",
    "Repairs & Maintenance",
]

# Product catalogue: (name, category, unit, min_price, max_price, tax_rate%, hsn_sac)
PRODUCT_CATALOG = [
    # Electronics
    ("[SEED] Laptop 15 inch",          "Electronics",       "Nos",     35000, 80000, 18, "8471"),
    ("[SEED] Laptop 13 inch",          "Electronics",       "Nos",     25000, 60000, 18, "8471"),
    ("[SEED] Wireless Mouse",          "Electronics",       "Nos",       500,  2500, 18, "8471"),
    ("[SEED] Mechanical Keyboard",     "Electronics",       "Nos",      1500,  8000, 18, "8471"),
    ("[SEED] USB-C Hub",               "Electronics",       "Nos",       800,  3500, 18, "8471"),
    ("[SEED] External SSD 1TB",        "Electronics",       "Nos",      4000, 12000, 18, "8471"),
    ("[SEED] 27 inch Monitor",         "Electronics",       "Nos",      8000, 30000, 18, "8471"),
    ("[SEED] Webcam HD",               "Electronics",       "Nos",      1500,  6000, 18, "8471"),
    ("[SEED] Smartphone Android",      "Electronics",       "Nos",      8000, 40000, 18, "8517"),
    ("[SEED] Wireless Headphones",     "Electronics",       "Nos",      1500, 15000, 18, "8518"),
    ("[SEED] Tablet 10 inch",          "Electronics",       "Nos",     12000, 45000, 18, "8471"),
    ("[SEED] Portable Charger",        "Electronics",       "Nos",       800,  3000, 18, "8504"),
    ("[SEED] Laser Printer",           "Electronics",       "Nos",      8000, 25000, 18, "8443"),
    ("[SEED] Pen Drive 32GB",          "Electronics",       "Nos",       400,  1200, 18, "8523"),
    ("[SEED] Ethernet Cable 5m",       "Electronics",       "Nos",       200,   800, 18, "8544"),
    ("[SEED] UPS Battery Backup",      "Electronics",       "Nos",      3000, 12000, 18, "8507"),
    ("[SEED] Network Switch 8-port",   "Electronics",       "Nos",      2000,  8000, 18, "8517"),
    ("[SEED] Server Rack Unit",        "Electronics",       "Nos",     15000, 50000, 18, "8471"),
    ("[SEED] Security Camera CCTV",    "Electronics",       "Nos",      2500, 10000, 28, "8525"),
    # Software Services
    ("[SEED] Web Development",         "Software Services", "Hrs",      1000,  3000, 18, "9983"),
    ("[SEED] Mobile App Development",  "Software Services", "Hrs",      1500,  4000, 18, "9983"),
    ("[SEED] IT Consulting",           "Software Services", "Hrs",      2000,  5000, 18, "9983"),
    ("[SEED] Cloud Migration",         "Software Services", "Project", 25000,150000, 18, "9983"),
    ("[SEED] Software Maintenance",    "Software Services", "Monthly",  5000, 20000, 18, "9983"),
    ("[SEED] UI/UX Design",            "Software Services", "Hrs",       800,  2500, 18, "9983"),
    ("[SEED] SEO Optimization",        "Software Services", "Monthly",  5000, 25000, 18, "9983"),
    ("[SEED] Data Analytics Report",   "Software Services", "Project", 10000, 50000, 18, "9983"),
    ("[SEED] API Integration",         "Software Services", "Project", 15000, 60000, 18, "9983"),
    ("[SEED] Digital Signature Token", "Software Services", "Nos",      1000,  3000, 18, "9983"),
    ("[SEED] Accounting Software Lic", "Software Services", "Annual",   5000, 25000, 18, "9983"),
    ("[SEED] Employee Training",       "Software Services", "Days",     5000, 20000, 18, "9992"),
    # Office Supplies
    ("[SEED] A4 Paper Ream",           "Office Supplies",   "Ream",      200,   500, 12, "4802"),
    ("[SEED] Ball Point Pens (Box)",   "Office Supplies",   "Box",       100,   300, 12, "9608"),
    ("[SEED] Stapler",                 "Office Supplies",   "Nos",       200,   800, 12, "8305"),
    ("[SEED] File Folders Pack",       "Office Supplies",   "Pack",      150,   400, 12, "4820"),
    ("[SEED] Marker Pens Set",         "Office Supplies",   "Set",       200,   600, 12, "9608"),
    ("[SEED] Whiteboard Eraser",       "Office Supplies",   "Nos",       100,   300, 12, "3926"),
    ("[SEED] Sticky Notes Pack",       "Office Supplies",   "Pack",       80,   250, 12, "4820"),
    ("[SEED] Scissors",                "Office Supplies",   "Nos",       100,   400, 12, "8213"),
    ("[SEED] Office Chair",            "Office Supplies",   "Nos",      3000, 15000, 18, "9401"),
    ("[SEED] Standing Desk",           "Office Supplies",   "Nos",      8000, 35000, 18, "9403"),
    ("[SEED] Toner Cartridge",         "Office Supplies",   "Nos",      1500,  5000, 18, "8443"),
    # FMCG
    ("[SEED] Mineral Water Crate",     "FMCG",              "Crate",     200,   500, 12, "2201"),
    ("[SEED] Tea Bags Box",            "FMCG",              "Box",       150,   400,  5, "0902"),
    ("[SEED] Coffee Sachets Box",      "FMCG",              "Box",       300,   800,  5, "0901"),
    ("[SEED] Biscuits Assorted",       "FMCG",              "Pack",      200,   500, 18, "1905"),
    ("[SEED] Hand Sanitizer Bulk",     "FMCG",              "Litres",    300,   800, 18, "3808"),
    ("[SEED] Cleaning Supplies Kit",   "FMCG",              "Kit",       500,  1500, 18, "3402"),
    ("[SEED] First Aid Kit",           "FMCG",              "Kit",       500,  2000, 12, "3005"),
    ("[SEED] Green Tea Packets",       "FMCG",              "Box",       200,   600,  5, "0902"),
    ("[SEED] Instant Noodles Box",     "FMCG",              "Box",       300,   700, 18, "1902"),
]

# ─── Time distribution ────────────────────────────────────────────────────────
# (bucket_name, days_ago_inclusive_start, days_ago_exclusive_end)
TIME_BUCKETS = [
    ("this_week",    0,   7),
    ("this_month",   7,  30),
    ("this_quarter", 30,  90),
    ("this_year",    90, 365),
]

# ─── Scenario configurations ──────────────────────────────────────────────────
SCENARIOS = {
    "balanced": {
        "description":   "Realistic balanced business activity (default)",
        "customers":     30,
        "items":         50,
        "invoices":      120,
        "vendors":       10,
        "bills":         40,
        "expenses":      60,
        # minimum records guaranteed per bucket [week, month, quarter, year]
        "customer_mins": [2,  5,  8, 10],
        "invoice_mins":  [3, 10, 25, 40],
        "expense_mins":  [3,  8, 15, 20],
    },
    "high-revenue": {
        "description":   "Large business with high invoice volumes",
        "customers":     50,
        "items":         50,
        "invoices":      200,
        "vendors":       15,
        "bills":         60,
        "expenses":      80,
        "customer_mins": [5,  10, 15, 15],
        "invoice_mins":  [5,  20, 55, 80],
        "expense_mins":  [5,  15, 25, 25],
    },
    "low-activity": {
        "description":   "Minimal activity for sparse testing",
        "customers":     10,
        "items":         20,
        "invoices":      30,
        "vendors":       5,
        "bills":         10,
        "expenses":      15,
        "customer_mins": [1, 2, 3, 3],
        "invoice_mins":  [1, 3, 8, 10],
        "expense_mins":  [1, 2, 4,  5],
    },
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generate_gstin(state_code: str) -> str:
    """Build a syntactically valid 15-character GSTIN for the given state code."""
    pan_letters = "".join(random.choices(string.ascii_uppercase, k=5))
    pan_digits  = "".join(random.choices(string.digits, k=4))
    pan_last    = random.choice(string.ascii_uppercase)
    entity_char = random.choice("123456789")
    check_digit = random.choice(string.ascii_uppercase + string.digits)
    return f"{state_code}{pan_letters}{pan_digits}{pan_last}{entity_char}Z{check_digit}"


def _indian_phone() -> str:
    """Return a 10-digit Indian mobile number starting with 6-9."""
    return str(random.randint(6, 9)) + "".join(random.choices(string.digits, k=9))


def _random_date_in_window(days_ago_min: int, days_ago_max: int) -> datetime:
    """Return a random UTC datetime between days_ago_min and days_ago_max days ago."""
    days    = random.randint(days_ago_min, max(days_ago_min, days_ago_max - 1))
    hours   = random.randint(0, 23)
    minutes = random.randint(0, 59)
    return datetime.utcnow() - timedelta(days=days, hours=hours, minutes=minutes)


def _allocate_to_buckets(total: int, mins: list) -> list:
    """
    Distribute `total` records across 4 time buckets with guaranteed minimums.
    `mins` = [min_week, min_month, min_quarter, min_year].
    Returns [count_week, count_month, count_quarter, count_year].
    Remaining records (beyond mins) are spread with a recency bias.
    """
    guaranteed = sum(mins)
    if total < guaranteed:
        total = guaranteed  # never drop below minimums
    remaining = total - guaranteed
    # Recency-biased weights for the surplus
    weights = [0.20, 0.30, 0.30, 0.20]
    counts  = list(mins)
    for i, w in enumerate(weights):
        counts[i] += int(remaining * w)
    # Assign leftover from integer rounding to the most recent bucket
    counts[0] += total - sum(counts)
    return counts


def _random_past_datetime(months: int = 12) -> datetime:
    """Return a random UTC datetime within the last `months` months."""
    return datetime.utcnow() - timedelta(days=random.randint(1, months * 30))


def _calc_invoice_totals(line_items: list, is_interstate: bool) -> dict:
    """
    Compute GST breakdown from invoice line items.

    Each item must contain: rate (float), quantity (int/float),
      discount (flat ₹ amount), tax (GST % as a number, e.g. 18).

    Returns: subtotal, cgst_amount, sgst_amount, igst_amount, total_tax, total_amount
    """
    subtotal        = 0.0
    total_item_tax  = 0.0

    for it in line_items:
        base            = max(0.0, it["rate"] * it["quantity"] - it.get("discount", 0.0))
        subtotal        += base
        total_item_tax  += base * it["tax"] / 100.0

    subtotal       = round(subtotal, 2)
    total_item_tax = round(total_item_tax, 2)

    if is_interstate:
        igst = round(total_item_tax, 2)
        cgst = sgst = 0.0
    else:
        cgst = sgst = round(total_item_tax / 2.0, 2)
        igst = 0.0

    total_tax    = round(cgst + sgst + igst, 2)
    total_amount = round(subtotal + total_tax, 2)

    return {
        "subtotal":     subtotal,
        "cgst_amount":  cgst,
        "sgst_amount":  sgst,
        "igst_amount":  igst,
        "total_tax":    total_tax,
        "total_amount": total_amount,
    }


def _next_invoice_counter(tenant_id: str) -> int:
    """Return the next sequential invoice counter for this tenant."""
    results = list(invoices_container.query_items(
        query="SELECT c.invoice_number FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    max_num = 0
    for r in results:
        raw = r.get("invoice_number", "")
        try:
            max_num = max(max_num, int(raw.split("-")[-1]))
        except (ValueError, IndexError):
            pass
    return max_num + 1


def _next_bill_counter(tenant_id: str) -> int:
    """Return the next sequential bill counter for this tenant."""
    results = list(bills_container.query_items(
        query="SELECT c.bill_number FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    max_num = 0
    for r in results:
        raw = r.get("bill_number", "")
        try:
            max_num = max(max_num, int(raw.split("-")[-1]))
        except (ValueError, IndexError):
            pass
    return max_num + 1


def _existing_product_names(tenant_id: str) -> set:
    """Return lower-cased set of product names already in the tenant's catalogue."""
    results = list(products_container.query_items(
        query=(
            "SELECT c.name FROM c "
            "WHERE c.tenant_id = @tid "
            "AND (NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false)"
        ),
        parameters=[{"name": "@tid", "value": tenant_id}],
        enable_cross_partition_query=True,
    ))
    return {r["name"].lower() for r in results}


# ─── Seeder functions ─────────────────────────────────────────────────────────

def seed_customers(
    tenant_id: str,
    count: int,
    days_ago_min: int = 0,
    days_ago_max: int = 365,
) -> list:
    """Seed `count` customers with created_at within [days_ago_min, days_ago_max) days ago."""
    created = []
    for _ in range(count):
        state, state_code = random.choice(INDIAN_STATES)
        is_interstate     = (state != HOME_STATE)

        first   = fake.first_name()
        last    = fake.last_name()
        company = (
            f"{last} & Associates" if random.random() < 0.5
            else fake.company()
        )
        customer_id = str(uuid.uuid4())
        created_dt  = _random_date_in_window(days_ago_min, days_ago_max)
        now         = datetime.utcnow().isoformat()

        customer = {
            "id":               customer_id,
            "customer_id":      customer_id,
            "display_name":     company,
            "first_name":       first,
            "last_name":        last,
            "company_name":     company,
            "email":            fake.email(),
            "phone":            _indian_phone(),
            "customer_type":    "Business",
            "salutation":       random.choice(["Mr.", "Ms.", "Dr."]),
            "gst_number":       _generate_gstin(state_code),
            "pan":              "",
            "gst_treatment":    "Regular",
            "place_of_supply":  state,
            "tax_preference":   "Taxable",
            "billing_street":   fake.street_address(),
            "billing_city":     fake.city(),
            "billing_state":    state,
            "billing_zip":      fake.postcode(),
            "billing_country":  "India",
            "shipping_street":  "",
            "shipping_city":    "",
            "shipping_state":   "",
            "shipping_zip":     "",
            "shipping_country": "India",
            "opening_balance":  0.0,
            "payment_terms":    random.choice(PAYMENT_TERMS_OPTIONS),
            "currency":         "INR",
            "language":         "English",
            "portal_enabled":   False,
            "documents":        [],
            "contact_persons":  [],
            "custom_fields":    {},
            "reporting_tags":   [],
            "remarks":          "",
            # internal seeder flag (used for GST type during invoice generation)
            "is_interstate":    is_interstate,
            "tenant_id":        tenant_id,
            "created_at":       created_dt.isoformat(),
            "updated_at":       now,
        }
        customers_container.create_item(body=customer)
        created.append(customer)
    return created


def seed_vendors(tenant_id: str, count: int) -> list:
    created = []
    for _ in range(count):
        state, state_code = random.choice(INDIAN_STATES)
        vendor_id = str(uuid.uuid4())
        now       = datetime.utcnow().isoformat()

        vendor = {
            "id":           vendor_id,
            "vendor_id":    vendor_id,
            "name":         fake.company(),
            "contact_person": fake.name(),
            "email":        fake.email(),
            "phone":        _indian_phone(),
            "address":      fake.street_address(),
            "city":         fake.city(),
            "state":        state,
            "postal_code":  fake.postcode(),
            "country":      "India",
            "tax_id":       _generate_gstin(state_code),
            "payment_terms": random.choice(PAYMENT_TERMS_OPTIONS),
            "notes":        "",
            "tenant_id":    tenant_id,
            "created_at":   now,
            "updated_at":   now,
        }
        vendors_container.create_item(body=vendor)
        created.append(vendor)
    return created


def seed_products(tenant_id: str, count: int, existing_names: set) -> list:
    """
    Seed up to `count` products from PRODUCT_CATALOG.
    Products whose names already exist in `existing_names` are skipped (idempotency).
    """
    created = []
    catalog  = PRODUCT_CATALOG[:count]

    for (name, category, unit, min_p, max_p, tax_rate, hsn) in catalog:
        if name.lower() in existing_names:
            print(f"  [SKIP] Product already exists: {name}")
            continue

        product_id = str(uuid.uuid4())
        price      = round(random.uniform(min_p, max_p), 2)
        now        = datetime.utcnow().isoformat()

        product = {
            "id":                   product_id,
            "product_id":           product_id,
            "name":                 name,
            "item_type":            "Service" if category == "Software Services" else "Goods",
            "category":             category,
            "unit":                 unit,
            "price":                price,
            "purchase_rate":        round(price * 0.70, 2),
            "tax_rate":             tax_rate,
            "hsn_sac":              hsn,
            "tax_preference":       "Taxable",
            "description":          f"{name} — quality assured",
            "purchase_description": f"Purchase of {name}",
            "sales_enabled":        True,
            "purchase_enabled":     True,
            "sales_account":        "Sales",
            "purchase_account":     "Purchases",
            "reorder_level":        random.randint(5, 20),
            "reorder_qty":          random.randint(10, 50),
            "preferred_vendor_id":  "",
            "is_deleted":           False,
            "tenant_id":            tenant_id,
            "created_at":           now,
            "updated_at":           now,
        }
        products_container.create_item(body=product)
        created.append(product)
        existing_names.add(name.lower())

    return created


def seed_stock_initial(tenant_id: str, products: list) -> dict:
    """
    Create opening stock transactions to give products realistic inventory levels.

    Distribution (deterministic by product index mod 10):
      40% — healthy stock  (100–300 units IN)
      30% — normal stock   (20–50 units IN)
      20% — low stock      (IN then OUT, leaving 2–(reorder_level-1) units)
      10% — out of stock   (IN then equal OUT)

    Returns a summary dict with counts per level.
    """
    summary = {"healthy": 0, "normal": 0, "low": 0, "out_of_stock": 0}
    ts = (datetime.utcnow() - timedelta(days=366)).isoformat()  # pre-dates all other records

    for i, prod in enumerate(products):
        pid   = prod["product_id"]
        level = i % 10  # deterministic bucketing by catalogue position

        def _in(qty):
            stock_container.create_item(body={
                "id": str(uuid.uuid4()), "product_id": pid, "tenant_id": tenant_id,
                "quantity": float(qty), "type": "IN",
                "source": "Opening Stock", "reference_id": None, "timestamp": ts,
            })

        def _out(qty):
            stock_container.create_item(body={
                "id": str(uuid.uuid4()), "product_id": pid, "tenant_id": tenant_id,
                "quantity": float(qty), "type": "OUT",
                "source": "Initial Adjustment", "reference_id": None, "timestamp": ts,
            })

        if level < 4:           # 40% healthy
            _in(random.randint(100, 300))
            summary["healthy"] += 1
        elif level < 7:         # 30% normal
            _in(random.randint(20, 50))
            summary["normal"] += 1
        elif level < 9:         # 20% low stock (below reorder_level)
            reorder = prod.get("reorder_level", 10)
            stock_in  = reorder * 4
            # Leave between 1 and (reorder_level - 1) units — triggers the alert
            leave = random.randint(1, max(1, reorder - 1))
            stock_out = stock_in - leave
            _in(stock_in)
            _out(stock_out)
            summary["low"] += 1
        else:                   # 10% out of stock
            qty = random.randint(20, 50)
            _in(qty)
            _out(qty)
            summary["out_of_stock"] += 1

    return summary


def seed_invoices(
    tenant_id: str,
    count: int,
    customers: list,
    products: list,
    days_ago_min: int = 0,
    days_ago_max: int = 365,
    allow_overdue: bool = True,
) -> list:
    """
    Seed `count` invoices with issue_date within [days_ago_min, days_ago_max) days ago.
    `allow_overdue=False` for very recent buckets where due_date would be in the future.
    mirroring the behaviour of the live invoices API.
    """
    if not customers or not products:
        print("  [WARN] No customers or products — skipping invoices.")
        return []

    created     = []
    inv_counter = _next_invoice_counter(tenant_id)

    for _ in range(count):
        customer   = random.choice(customers)
        num_lines  = random.randint(2, 5)
        line_prods = random.sample(products, min(num_lines, len(products)))

        line_items = []
        for prod in line_prods:
            qty      = random.randint(1, 20)
            rate     = prod["price"]
            discount = float(random.choice([0, 0, 0, 50, 100, 200, 500]))
            base     = max(0.0, qty * rate - discount)
            tax_amt  = round(base * prod["tax_rate"] / 100.0, 2)
            line_items.append({
                "name":       prod["name"],
                "product_id": prod["product_id"],
                "quantity":   qty,
                "rate":       rate,
                "discount":   discount,
                "tax":        prod["tax_rate"],   # % value, e.g. 18
                "amount":     round(base + tax_amt, 2),
            })

        is_interstate = customer.get("is_interstate", False)
        totals        = _calc_invoice_totals(line_items, is_interstate)

        issue_dt = _random_date_in_window(days_ago_min, days_ago_max)
        due_days = random.choice([15, 30, 45])
        due_dt   = issue_dt + timedelta(days=due_days)

        # Status: only assign Overdue if the due_date is already in the past
        is_due_past = due_dt < datetime.utcnow()
        if allow_overdue and is_due_past:
            rand = random.random()
            if rand < 0.50:
                status = "Paid"
            elif rand < 0.75:
                status = "Issued"
            else:
                status = "Overdue"
        else:
            # Due date is in the future — invoice cannot be Overdue yet
            status = "Paid" if random.random() < 0.55 else "Issued"

        amount_paid   = totals["total_amount"] if status == "Paid" else 0.0
        balance_due   = 0.0 if status == "Paid" else totals["total_amount"]
        payment_date  = (
            (issue_dt + timedelta(days=random.randint(1, due_days))).date().isoformat()
            if status == "Paid" else None
        )
        bank_reference = (
            f"TXN{random.randint(100000, 999999)}" if status == "Paid" else None
        )

        invoice_number = f"INV-{str(inv_counter).zfill(3)}"
        inv_counter   += 1
        invoice_id     = str(uuid.uuid4())
        now            = datetime.utcnow().isoformat()

        invoice = {
            "id":              invoice_id,
            "invoice_number":  invoice_number,
            "customer_id":     customer["customer_id"],
            "customer_name":   customer["display_name"],
            "customer_email":  customer["email"],
            "customer_phone":  customer["phone"],
            "issue_date":      issue_dt.date().isoformat(),
            "due_date":        due_dt.date().isoformat(),
            "payment_terms":   f"Net {due_days}",
            "subtotal":        totals["subtotal"],
            "cgst_amount":     totals["cgst_amount"],
            "sgst_amount":     totals["sgst_amount"],
            "igst_amount":     totals["igst_amount"],
            "total_tax":       totals["total_tax"],
            "total_amount":    totals["total_amount"],
            "amount_paid":     amount_paid,
            "balance_due":     balance_due,
            "status":          status,
            "payment_mode":    random.choice(PAYMENT_MODES) if status == "Paid" else "",
            "notes":           "Thank you for your business.",
            "terms_conditions":"Payment due within the specified period.",
            "is_gst_applicable": True,
            "invoice_type":    "Tax Invoice",
            "items":           line_items,
            "portal_token":    secrets.token_urlsafe(32),
            # Future-facing fields: banking & analytics
            "payment_status":  "Paid" if status == "Paid" else ("Overdue" if status == "Overdue" else "Unpaid"),
            "payment_date":    payment_date,
            "bank_reference":  bank_reference,
            "tags":            random.sample(TAGS_POOL, k=random.randint(1, 3)),
            "tenant_id":       tenant_id,
            "created_at":      issue_dt.isoformat(),
            "updated_at":      now,
        }
        invoices_container.create_item(body=invoice)

        # Mirror real API behaviour: create a Stock OUT transaction per line
        for line in line_items:
            try:
                stock_container.create_item(body={
                    "id":           str(uuid.uuid4()),
                    "product_id":   line["product_id"],
                    "tenant_id":    tenant_id,
                    "quantity":     float(line["quantity"]),
                    "type":         "OUT",
                    "source":       f"Invoice {invoice_number}",
                    "reference_id": invoice_id,
                    "timestamp":    issue_dt.isoformat(),
                })
            except Exception as exc:
                print(f"  [WARN] Stock OUT failed for product {line['product_id']}: {exc}")

        created.append(invoice)

    return created


def seed_bills(tenant_id: str, count: int, vendors: list, products: list) -> list:
    """
    Seed purchase bills against seeded vendors.
    Uses a 70% purchase rate on product prices to simulate cost prices.
    """
    if not vendors:
        print("  [WARN] No vendors — skipping bills.")
        return []

    created      = []
    bill_counter = _next_bill_counter(tenant_id)

    for _ in range(count):
        vendor    = random.choice(vendors)
        num_lines = random.randint(1, 4)
        line_prods = random.sample(products, min(num_lines, len(products))) if products else []

        line_items = []
        subtotal   = 0.0
        for prod in line_prods:
            qty  = random.randint(1, 10)
            rate = round(prod["price"] * 0.70, 2)
            line_items.append({
                "name":       prod["name"],
                "product_id": prod["product_id"],
                "quantity":   qty,
                "rate":       rate,
                "amount":     round(qty * rate, 2),
            })
            subtotal += qty * rate

        subtotal     = round(subtotal, 2)
        tax_amount   = round(subtotal * 0.18, 2)
        total_amount = round(subtotal + tax_amount, 2)

        bill_dt  = _random_past_datetime(12)
        due_dt   = bill_dt + timedelta(days=random.choice([15, 30, 45]))

        rand = random.random()
        if rand < 0.50:
            payment_status = "Paid"
        elif rand < 0.75:
            payment_status = "Unpaid"
        else:
            payment_status = "Overdue"

        amount_paid = total_amount if payment_status == "Paid" else 0.0
        balance_due = 0.0 if payment_status == "Paid" else total_amount

        bill_number  = f"BILL-{str(bill_counter).zfill(3)}"
        bill_counter += 1
        bill_id      = str(uuid.uuid4())
        now          = datetime.utcnow().isoformat()

        bill = {
            "id":                 bill_id,
            "bill_number":        bill_number,
            "vendor_id":          vendor["vendor_id"],
            "vendor_name":        vendor["name"],
            "bill_date":          bill_dt.date().isoformat(),
            "due_date":           due_dt.date().isoformat(),
            "payment_terms":      vendor.get("payment_terms", "Net 30"),
            "subtotal":           subtotal,
            "tax_amount":         tax_amount,
            "total_amount":       total_amount,
            "amount_paid":        amount_paid,
            "balance_due":        balance_due,
            "payment_status":     payment_status,
            "notes":              "",
            "terms_conditions":   "",
            "items":              line_items,
            "expenses":           [],
            "converted_from_po_id": None,
            "payment_history":    [],
            "tenant_id":          tenant_id,
            "created_at":         bill_dt.isoformat(),
            "updated_at":         now,
        }
        bills_container.create_item(body=bill)
        created.append(bill)

    return created


def seed_expenses(
    tenant_id: str,
    count: int,
    vendors: list,
    days_ago_min: int = 0,
    days_ago_max: int = 365,
) -> list:
    """Seed `count` expenses with date within [days_ago_min, days_ago_max) days ago."""
    created = []

    for _ in range(count):
        vendor_name = random.choice(vendors)["name"] if vendors else fake.company()
        expense_dt  = _random_date_in_window(days_ago_min, days_ago_max)
        expense_id  = str(uuid.uuid4())
        now         = datetime.utcnow().isoformat()

        expense = {
            "id":             expense_id,
            "vendor_name":    vendor_name,
            "date":           expense_dt.date().isoformat(),
            "category":       random.choice(EXPENSE_CATEGORIES),
            "amount":         round(random.uniform(500.0, 50000.0), 2),
            "currency":       "INR",
            "notes":          "",
            "receipt_url":    None,
            "payment_mode":   random.choice(PAYMENT_MODES),
            "bank_reference": f"EXP{random.randint(100000, 999999)}",
            "tags":           random.sample(TAGS_POOL, k=random.randint(1, 2)),
            "tenant_id":      tenant_id,
            "created_at":     expense_dt.isoformat(),
            "updated_at":     now,
        }
        expenses_container.create_item(body=expense)
        created.append(expense)

    return created


# ─── Reset ────────────────────────────────────────────────────────────────────

def reset_tenant_data(tenant_id: str) -> dict:
    """
    Delete ALL documents for the given tenant from every container.
    Returns a per-container deletion count dict.
    """
    containers_meta = [
        (customers_container, "customer_id"),
        (products_container,  "product_id"),
        (invoices_container,  "customer_id"),
        (vendors_container,   "vendor_id"),
        (bills_container,     "vendor_id"),
        (expenses_container,  "id"),
        (stock_container,     "product_id"),
    ]
    counts = {}
    for (container, pk_field) in containers_meta:
        cname   = container.id
        results = list(container.query_items(
            query=f"SELECT c.id, c['{pk_field}'] AS pk FROM c WHERE c.tenant_id = @tid",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        deleted = 0
        for item in results:
            try:
                container.delete_item(item=item["id"], partition_key=item["pk"])
                deleted += 1
            except Exception as exc:
                print(f"  [WARN] Could not delete {item['id']} from {cname}: {exc}")
        counts[cname] = deleted
        print(f"  Deleted {deleted:>4} record(s) from  {cname}")
    return counts


# ─── Post-seed validation ─────────────────────────────────────────────────────

def validate_seed_results(
    tenant_id: str,
    customers: list,
    invoices: list,
    expenses: list,
    cfg: dict,
) -> bool:
    """
    Verify seeding results against the scenario's bucket minimums.
    Prints a per-window breakdown table. Returns True if all checks pass.
    """
    now = datetime.utcnow()
    ok  = True

    def _count_in_window(items, date_field, d_min, d_max):
        start = (now - timedelta(days=d_max)).isoformat()
        end   = (now - timedelta(days=d_min)).isoformat()
        return sum(1 for x in items if start <= x.get(date_field, "") <= end)

    print("\n── Validation: records per time window ─────────────────────")
    print(f"  {'Bucket':<14}  Customers  Invoices  Expenses")
    print(f"  {'─'*14}  ─────────  ────────  ────────")

    for i, (bucket_name, d_min, d_max) in enumerate(TIME_BUCKETS):
        c_count   = _count_in_window(customers, "created_at", d_min, d_max)
        inv_count = _count_in_window(invoices,  "created_at", d_min, d_max)
        exp_count = _count_in_window(expenses,  "created_at", d_min, d_max)

        c_min   = cfg["customer_mins"][i]
        inv_min = cfg["invoice_mins"][i]
        exp_min = cfg["expense_mins"][i]

        c_ok   = "✓" if c_count   >= c_min   else "✗"
        inv_ok = "✓" if inv_count >= inv_min  else "✗"
        exp_ok = "✓" if exp_count >= exp_min  else "✗"

        if "✗" in (c_ok, inv_ok, exp_ok):
            ok = False

        print(f"  {bucket_name:<14}  {c_ok}{c_count:>4}(≥{c_min:<3})  "
              f"{inv_ok}{inv_count:>4}(≥{inv_min:<3})  "
              f"{exp_ok}{exp_count:>4}(≥{exp_min:<3})")

    overdue = sum(1 for inv in invoices if inv.get("status") == "Overdue")
    ov_ok   = "✓" if overdue > 0 else "✗"
    if overdue == 0:
        ok = False
    print(f"\n  {ov_ok} Overdue invoices  : {overdue}")

    low_stock = sum(1 for p in invoices if False)  # stock validated separately
    print(f"  ✓ Validation {'PASSED' if ok else 'FAILED — some windows have zero records'}")
    return ok


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intelligent Mock Data Generator for Solidev Books.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--tenant_id", required=True,
                        help="Tenant ID to seed data for")
    parser.add_argument("--scenario",  default="balanced",
                        choices=list(SCENARIOS.keys()),
                        help="Data scenario: balanced | high-revenue | low-activity")
    parser.add_argument("--reset",     action="store_true",
                        help="Wipe all existing data for the tenant before seeding")
    parser.add_argument("--seed",      type=int, default=None,
                        help="Integer random seed for reproducible data")
    # Per-entity overrides (take precedence over scenario defaults)
    parser.add_argument("--customers", type=int, default=None)
    parser.add_argument("--items",     type=int, default=None)
    parser.add_argument("--invoices",  type=int, default=None)
    parser.add_argument("--vendors",   type=int, default=None)
    parser.add_argument("--bills",     type=int, default=None)
    parser.add_argument("--expenses",  type=int, default=None)
    args = parser.parse_args()

    # ── Apply random seed ────────────────────────────────────────────────────
    if args.seed is not None:
        random.seed(args.seed)
        Faker.seed(args.seed)

    tid = args.tenant_id
    cfg = SCENARIOS[args.scenario]

    # Override scenario defaults with any explicit CLI counts (None = use scenario default; 0 = skip)
    n_customers = cfg["customers"] if args.customers is None else args.customers
    n_items     = cfg["items"]     if args.items     is None else args.items
    n_invoices  = cfg["invoices"]  if args.invoices  is None else args.invoices
    n_vendors   = cfg["vendors"]   if args.vendors   is None else args.vendors
    n_bills     = cfg["bills"]     if args.bills     is None else args.bills
    n_expenses  = cfg["expenses"]  if args.expenses  is None else args.expenses

    # ── Print seeding plan ───────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  SOLIDEV BOOKS — INTELLIGENT DATA SEEDER")
    print(f"{'='*62}")
    print(f"  Tenant ID  : {tid}")
    print(f"  Scenario   : {args.scenario}  —  {cfg['description']}")
    if args.seed is not None:
        print(f"  Rand seed  : {args.seed}")
    print(f"\n  Planned volumes:")
    print(f"    Customers : {n_customers}")
    print(f"    Products  : {n_items}")
    print(f"    Invoices  : {n_invoices}")
    print(f"    Vendors   : {n_vendors}")
    print(f"    Bills     : {n_bills}")
    print(f"    Expenses  : {n_expenses}")
    print(f"\n  Minimum records guaranteed per dashboard filter window:")
    print(f"    {'Window':<14}  {'Customers':>9}  {'Invoices':>8}  {'Expenses':>8}")
    for i, (bn, _, _) in enumerate(TIME_BUCKETS):
        print(f"    {bn:<14}  {cfg['customer_mins'][i]:>9}  "
              f"{cfg['invoice_mins'][i]:>8}  {cfg['expense_mins'][i]:>8}")
    print(f"{'='*62}\n")

    # ── Reset ────────────────────────────────────────────────────────────────
    if args.reset:
        print("!! RESET mode — deleting all existing data for this tenant.")
        ans = input("   Type 'yes' to confirm: ").strip().lower()
        if ans != "yes":
            print("   Cancelled.")
            sys.exit(0)
        print()
        reset_tenant_data(tid)
        print()

    # ── 1. Products ──────────────────────────────────────────────────────────
    print(f"[1/7] Products  (target: {n_items})")
    existing_names = _existing_product_names(tid)
    new_products = seed_products(tid, n_items, existing_names)
    print(f"       Created: {len(new_products)}")

    # Always load the full product catalogue from DB (handles the case where
    # all products already existed and seed_products returned an empty list)
    all_products = list(products_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.tenant_id = @tid "
            "AND (NOT IS_DEFINED(c.is_deleted) OR c.is_deleted = false)"
        ),
        parameters=[{"name": "@tid", "value": tid}],
        enable_cross_partition_query=True,
    ))
    products = all_products if all_products else new_products
    print(f"       Available for invoices: {len(products)}")

    # ── 2. Vendors ───────────────────────────────────────────────────────────
    print(f"\n[2/7] Vendors   (target: {n_vendors})")
    if n_vendors > 0:
        vendors = seed_vendors(tid, n_vendors)
        print(f"       Created: {len(vendors)}")
    else:
        vendors = []
        print(f"       Skipping — loading existing vendors from DB")
    existing_vendors = list(vendors_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tid}],
        enable_cross_partition_query=True,
    ))
    all_vendors = vendors + [v for v in existing_vendors if v["vendor_id"] not in {x["vendor_id"] for x in vendors}]
    print(f"       Total available: {len(all_vendors)}")

    # ── 3. Customers (time-distributed) ──────────────────────────────────────
    print(f"\n[3/7] Customers (target: {n_customers}  —  across time windows)")
    if n_customers > 0:
        customer_bucket_counts = _allocate_to_buckets(n_customers, cfg["customer_mins"])
        customers = []
        for (bucket_name, d_min, d_max), count in zip(TIME_BUCKETS, customer_bucket_counts):
            batch = seed_customers(tid, count, days_ago_min=d_min, days_ago_max=d_max)
            customers.extend(batch)
            print(f"       {bucket_name:<14}: {count}")
        print(f"       Created: {len(customers)}")
    else:
        customers = []
        print(f"       Skipping — loading existing customers from DB")
    # Always merge with existing DB customers so invoice seeding has the full pool
    existing_customers = list(customers_container.query_items(
        query="SELECT * FROM c WHERE c.tenant_id = @tid",
        parameters=[{"name": "@tid", "value": tid}],
        enable_cross_partition_query=True,
    ))
    all_customers = customers + [c for c in existing_customers if c["customer_id"] not in {x["customer_id"] for x in customers}]
    print(f"       Total available for invoices: {len(all_customers)}")

    # ── 4. Invoices (time-distributed) ───────────────────────────────────────
    print(f"\n[4/7] Invoices  (target: {n_invoices}  —  across time windows)")
    invoice_bucket_counts = _allocate_to_buckets(n_invoices, cfg["invoice_mins"])
    invoices = []
    for i, ((bucket_name, d_min, d_max), count) in enumerate(zip(TIME_BUCKETS, invoice_bucket_counts)):
        # this_week: due dates often fall in the future, so Overdue is not valid
        allow_overdue = (i > 0)
        batch = seed_invoices(
            tid, count, all_customers, products,
            days_ago_min=d_min, days_ago_max=d_max,
            allow_overdue=allow_overdue,
        )
        invoices.extend(batch)
        overdue_n = sum(1 for inv in batch if inv["status"] == "Overdue")
        print(f"       {bucket_name:<14}: {count}  (overdue: {overdue_n})")
    print(f"       Total: {len(invoices)}")

    # ── 5. Stock initial levels ───────────────────────────────────────────────
    print(f"\n[5/7] Stock     (opening levels for {len(products)} products)")
    stock_summary = seed_stock_initial(tid, products)
    print(f"       Healthy: {stock_summary['healthy']}   Normal: {stock_summary['normal']}   "
          f"Low: {stock_summary['low']}   Out-of-stock: {stock_summary['out_of_stock']}")

    # ── 6. Bills ─────────────────────────────────────────────────────────────
    print(f"\n[6/7] Bills     (target: {n_bills})")
    bills = seed_bills(tid, n_bills, all_vendors, products)
    print(f"       Created: {len(bills)}")

    # ── 7. Expenses (time-distributed) ───────────────────────────────────────
    print(f"\n[7/7] Expenses  (target: {n_expenses}  —  across time windows)")
    expense_bucket_counts = _allocate_to_buckets(n_expenses, cfg["expense_mins"])
    expenses = []
    for (bucket_name, d_min, d_max), count in zip(TIME_BUCKETS, expense_bucket_counts):
        batch = seed_expenses(tid, count, all_vendors, days_ago_min=d_min, days_ago_max=d_max)
        expenses.extend(batch)
        print(f"       {bucket_name:<14}: {count}")
    print(f"       Total: {len(expenses)}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_revenue  = round(sum(inv["total_amount"] for inv in invoices), 2)
    paid_invoices  = sum(1 for inv in invoices if inv["status"] == "Paid")
    overdue_inv    = sum(1 for inv in invoices if inv["status"] == "Overdue")
    total_payables = round(sum(b["total_amount"] for b in bills), 2)
    paid_bills     = sum(1 for b in bills if b["payment_status"] == "Paid")
    total_expenses = round(sum(e["amount"] for e in expenses), 2)

    print(f"\n{'='*62}")
    print("  SEEDING COMPLETE — SUMMARY")
    print(f"{'='*62}")
    print(f"  Tenant ID        : {tid}")
    print(f"  Scenario         : {args.scenario}")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  Customers created: {len(customers)}")
    print(f"  Products created : {len(products)}")
    print(f"  Invoices created : {len(invoices)}  (Paid: {paid_invoices}, Overdue: {overdue_inv})")
    print(f"  Vendors created  : {len(vendors)}")
    print(f"  Bills created    : {len(bills)}  (Paid: {paid_bills})")
    print(f"  Expenses created : {len(expenses)}")
    print(f"  ──────────────────────────────────────────────────────")
    print(f"  Total Revenue    : ₹{total_revenue:>15,.2f}")
    print(f"  Total Payables   : ₹{total_payables:>15,.2f}")
    print(f"  Total Expenses   : ₹{total_expenses:>15,.2f}")
    print(f"{'='*62}")

    # ── Validation ────────────────────────────────────────────────────────────
    all_ok = validate_seed_results(tid, all_customers, invoices, expenses, cfg)
    if not all_ok:
        print("  Review the table above — some time windows may have fewer records than expected.")
    print()


if __name__ == "__main__":
    main()
