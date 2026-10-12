"""Full data export: a tenant's customers, invoices, bills and payments as CSV files in one zip.

Pure builder (no Cosmos access) so it can be tested with example data.
"""

import csv
import io
import json
import zipfile

# Cosmos system fields and secrets that never leave the system.
_EXCLUDED_FIELDS = {
    "_rid", "_self", "_etag", "_attachments", "_ts",
    "password", "hashed_pw", "portal_password", "portal_token",
    "token", "refresh_token",
}
# Any field whose name contains one of these is treated as a secret too, so new ones are not exported by accident.
_SECRET_NAME_PARTS = ("password", "token", "secret", "otp")

# Text starting with one of these is run as a formula by Excel/Sheets (CSV injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _is_excluded(key):
    lowered = key.lower()
    return key in _EXCLUDED_FIELDS or any(part in lowered for part in _SECRET_NAME_PARTS)

# Columns that lead each file, when present; the rest follow alphabetically.
_LEADING_COLUMNS = ["id", "number", "invoice_number", "bill_number", "display_name", "date"]

PAYMENT_COLUMNS = [
    "direction", "payment_id", "payment_date", "amount", "payment_mode", "reference",
    "document_type", "document_id", "document_number", "party_name",
]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, (bool, int, float)):
        # Numbers (including negative amounts) stay numbers.
        return str(value)
    else:
        text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _columns(rows):
    keys = set()
    for row in rows:
        keys.update(k for k in row if not _is_excluded(k))
    leading = [c for c in _LEADING_COLUMNS if c in keys]
    return leading + sorted(keys - set(leading))


def rows_to_csv(rows, columns=None):
    """Serialise a list of dicts to CSV text; nested values become JSON."""
    rows = list(rows or [])
    columns = columns or _columns(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(col)) for col in columns])
    return output.getvalue()


def _payments_from(docs, direction, document_type, number_key, party_key):
    rows = []
    for doc in docs:
        for entry in doc.get("payment_history") or []:
            rows.append({
                "direction": direction,
                "payment_id": entry.get("id", ""),
                "payment_date": (entry.get("payment_date") or "")[:10],
                "amount": entry.get("amount", 0),
                "payment_mode": entry.get("payment_mode", ""),
                "reference": entry.get("reference", ""),
                "document_type": document_type,
                "document_id": doc.get("id", ""),
                "document_number": doc.get(number_key, ""),
                "party_name": doc.get(party_key, ""),
            })
    return rows


def payment_rows(invoices, bills):
    """Payments received (invoice payment history) and made (bill payment history)."""
    rows = _payments_from(invoices, "received", "invoice", "invoice_number", "customer_name")
    rows += _payments_from(bills, "made", "bill", "bill_number", "vendor_name")
    rows.sort(key=lambda r: (r["payment_date"], r["direction"], r["document_number"]))
    return rows


def build_export_zip(customers, invoices, bills, exported_at):
    """Return the zip bytes: customers.csv, invoices.csv, bills.csv, payments.csv, README.txt."""
    files = {
        "customers.csv": rows_to_csv(customers),
        "invoices.csv": rows_to_csv(invoices),
        "bills.csv": rows_to_csv(bills),
        "payments.csv": rows_to_csv(payment_rows(invoices, bills), PAYMENT_COLUMNS),
    }
    readme = (
        "Solidev Books data export\n"
        f"Exported at: {exported_at}\n"
        f"customers.csv: {len(customers)} rows\n"
        f"invoices.csv: {len(invoices)} rows (line items are in the 'items' column as JSON)\n"
        f"bills.csv: {len(bills)} rows (line items are in the 'items' column as JSON)\n"
        "payments.csv: payments received against invoices and made against bills\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            # BOM so Excel opens UTF-8 (₹, Indian names) correctly.
            zf.writestr(name, "﻿" + text)
        zf.writestr("README.txt", readme)
    return buf.getvalue()
