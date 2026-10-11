"""
tax_invoices.py
===============
GST tax invoices that Solidev issues for each paid Solidev Books subscription charge.

Numbers run per Indian financial year (April–March), e.g. SB/2026-27/0001. Cosmos has no
sequences, so a `counters` document per year is advanced with an ETag-conditional replace:
two webhooks can never take the same number.

Tax: the charged amount includes 18% GST.
  taxable = round(total / 1.18, 2); tax = total − taxable
  buyer in the seller's state → CGST + SGST (half each; the odd paisa goes to SGST), else IGST.

Configuration (names only; values live in the app settings):
  SELLER_LEGAL_NAME, SELLER_CIN, SELLER_GSTIN, SELLER_ADDRESS_LINES (lines separated by "|"),
  SELLER_EMAIL (optional), BILLING_SAC_CODE, INVOICE_SERIES_PREFIX (default "SB")
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from azure.core import MatchConditions
from azure.cosmos import exceptions

from smart_invoice_pro.utils.cosmos_client import counters_container, tax_invoices_container

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
GST_RATE = Decimal("0.18")
PAISA = Decimal("0.01")
REQUIRED_CONFIG = ("SELLER_LEGAL_NAME", "SELLER_CIN", "SELLER_GSTIN", "SELLER_ADDRESS_LINES", "BILLING_SAC_CODE")
COUNTER_RETRIES = 10

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STATE_NAMES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}
_STATE_CODES = {name.lower(): code for code, name in STATE_NAMES.items()}
_STATE_CODES.update({"daman & diu": "26", "dadra & nagar haveli": "26", "jammu and kashmir": "01",
                     "andaman and nicobar islands": "35", "orissa": "21", "pondicherry": "34"})


class TaxInvoiceConfigError(Exception):
    pass


class CounterBusyError(Exception):
    """The year's counter stayed contended through every retry; the caller should retry later."""


# ── Config ────────────────────────────────────────────────────────────────────
def missing_config() -> list[str]:
    """Names of required seller settings that are not set."""
    return [name for name in REQUIRED_CONFIG if not (os.getenv(name) or "").strip()]


def seller_details() -> dict:
    missing = missing_config()
    if missing:
        raise TaxInvoiceConfigError(", ".join(missing))
    gstin = os.getenv("SELLER_GSTIN").strip().upper()
    return {
        "legal_name": os.getenv("SELLER_LEGAL_NAME").strip(),
        "cin": os.getenv("SELLER_CIN").strip(),
        "gstin": gstin,
        "address_lines": [line.strip() for line in os.getenv("SELLER_ADDRESS_LINES").split("|") if line.strip()],
        "email": (os.getenv("SELLER_EMAIL") or "").strip(),
        "state_code": gstin[:2],
        "state": STATE_NAMES.get(gstin[:2], ""),
    }


# ── Pure helpers ──────────────────────────────────────────────────────────────
def financial_year(day) -> str:
    """'2026-27' for any date from 1 Apr 2026 to 31 Mar 2027."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def ist_date(unix_seconds=None):
    """The IST calendar date of a unix time (now when missing)."""
    try:
        moment = datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        moment = datetime.now(timezone.utc)
    return moment.astimezone(IST).date()


def split_tax(total: Decimal, intra_state: bool) -> dict:
    """Taxable value and GST heads for a GST-inclusive total; the parts always add up to total."""
    total = Decimal(total).quantize(PAISA)
    taxable = (total / (1 + GST_RATE)).quantize(PAISA, rounding=ROUND_HALF_UP)
    tax = total - taxable
    if intra_state:
        cgst = (tax / 2).quantize(PAISA, rounding=ROUND_DOWN)
        heads = {"cgst": cgst, "sgst": tax - cgst, "igst": Decimal("0.00")}
    else:
        heads = {"cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": tax}
    return {"taxable_value": taxable, "total_tax": tax, "total": total, **heads}


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return " ".join(w for w in (_TENS[n // 10], _ONES[n % 10]) if w)


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{_ONES[hundreds]} Hundred"] if hundreds else []
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        count, n = divmod(n, size)
        if count:
            parts.append(f"{_indian_words(count) if count >= 100 else _below_hundred(count)} {name}")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    """'Rupees Seven Hundred Seven and Eighteen Paise Only' (Indian numbering)."""
    amount = Decimal(amount).quantize(PAISA)
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = f"Rupees {_indian_words(rupees)}"
    if paise:
        words += f" and {_below_hundred(paise)} Paise"
    return words + " Only"


def buyer_details(tenant: dict, profile: dict | None) -> dict:
    """Buyer block from the organization profile; the state comes from the GSTIN first, then the address."""
    profile = profile or {}
    address = profile.get("address") or {}
    gstin = (profile.get("gstin") or "").strip().upper()
    if not GSTIN_PATTERN.match(gstin):
        gstin = ""
    state_code = gstin[:2] if gstin else _STATE_CODES.get((address.get("state") or "").strip().lower(), "")
    lines = [address.get(k, "").strip() for k in ("line1", "line2", "city") if (address.get(k) or "").strip()]
    tail = " ".join(p for p in ((address.get("state") or "").strip(), (address.get("pincode") or "").strip()) if p)
    if tail:
        lines.append(tail)
    return {
        "name": (profile.get("organization_name") or "").strip() or tenant.get("name") or "Solidev Books customer",
        "gstin": gstin,
        "registration": "Registered" if gstin else "Unregistered",
        "address_lines": lines,
        "state_code": state_code,
        "state": STATE_NAMES.get(state_code, (address.get("state") or "").strip()),
    }


# ── Numbering ─────────────────────────────────────────────────────────────────
def next_number(fy: str) -> int:
    """Take the next number for this financial year; the ETag check makes concurrent callers retry."""
    counter_id = f"tax_invoice:{fy}"
    for _ in range(COUNTER_RETRIES):
        try:
            counter = counters_container.read_item(item=counter_id, partition_key=counter_id)
        except exceptions.CosmosResourceNotFoundError:
            try:
                counters_container.create_item(body={"id": counter_id, "value": 1})
                return 1
            except exceptions.CosmosResourceExistsError:
                continue
        value = int(counter.get("value") or 0) + 1
        counter["value"] = value
        try:
            counters_container.replace_item(item=counter_id, body=counter, etag=counter.get("_etag"),
                                             match_condition=MatchConditions.IfNotModified)
            return value
        except exceptions.CosmosAccessConditionFailedError:
            continue
    raise CounterBusyError(f"tax invoice counter {fy} is busy")


def format_number(fy: str, value: int) -> str:
    prefix = (os.getenv("INVOICE_SERIES_PREFIX") or "SB").strip() or "SB"
    return f"{prefix}/{fy}/{value:04d}"


# ── Create ────────────────────────────────────────────────────────────────────
def _money(value: Decimal) -> float:
    return float(value)


def create_for_payment(tenant: dict, profile: dict | None, subscription: dict, payment: dict,
                       plan_code: str, period: str) -> dict | None:
    """
    The tax invoice for one captured subscription payment, created once per payment id.
    Returns the stored invoice, or None when the seller settings are missing.
    """
    payment_id = payment.get("id")
    invoice_id = f"tinv_{payment_id}"
    try:
        return tax_invoices_container.read_item(item=invoice_id, partition_key=tenant["id"])
    except exceptions.CosmosResourceNotFoundError:
        pass

    try:
        seller = seller_details()
    except TaxInvoiceConfigError as exc:
        logger.error("tax invoice: not created for payment %s, settings missing: %s", payment_id, exc)
        return None

    buyer = buyer_details(tenant, profile)
    # Place of supply is the buyer's state; with no known state it is the supplier's location.
    place_code = buyer["state_code"] or seller["state_code"]
    intra = place_code == seller["state_code"]
    amounts = split_tax(Decimal(int(payment["amount"])) / 100, intra)

    issue_day = ist_date(payment.get("created_at"))
    fy = financial_year(issue_day)
    number = format_number(fy, next_number(fy))
    period_start = ist_date(subscription.get("current_start")) if subscription.get("current_start") else None
    period_end = ist_date(subscription.get("current_end")) if subscription.get("current_end") else None
    plan_label = f"{plan_code.title()} {period}"
    description = f"Solidev Books {plan_label} subscription"
    if period_start and period_end:
        description += f", {period_start:%d %b %Y} – {period_end:%d %b %Y}"

    invoice = {
        "id": invoice_id,
        "tenant_id": tenant["id"],
        "number": number,
        "financial_year": fy,
        "issue_date": issue_day.isoformat(),
        "seller": seller,
        "buyer": buyer,
        "place_of_supply": {"state_code": place_code, "state": STATE_NAMES.get(place_code, buyer["state"])},
        "supply_type": "intra-state" if intra else "inter-state",
        "reverse_charge": False,
        "sac": os.getenv("BILLING_SAC_CODE").strip(),
        "description": description,
        "plan_code": plan_code,
        "period": period,
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "currency": "INR",
        "taxable_value": _money(amounts["taxable_value"]),
        "cgst_rate": 9 if intra else 0,
        "sgst_rate": 9 if intra else 0,
        "igst_rate": 0 if intra else 18,
        "cgst": _money(amounts["cgst"]),
        "sgst": _money(amounts["sgst"]),
        "igst": _money(amounts["igst"]),
        "total_tax": _money(amounts["total_tax"]),
        "total": _money(amounts["total"]),
        "amount_in_words": amount_in_words(amounts["total"]),
        "payment_ref": payment_id,
        "subscription_id": subscription.get("id"),
        "created_at": datetime.utcnow().isoformat(),
    }
    # TODO(SE-4104): email the PDF to the tenant's billing contact once server-side email exists.
    try:
        tax_invoices_container.create_item(body=invoice)
    except exceptions.CosmosResourceExistsError:
        # A concurrent event for the same payment won; its invoice stands and this number is unused.
        logger.warning("tax invoice: %s already exists; number %s not used", invoice_id, number)
        return tax_invoices_container.read_item(item=invoice_id, partition_key=tenant["id"])
    logger.info("tax invoice: %s issued to tenant %s for payment %s", number, tenant["id"], payment_id)
    return invoice


def list_for_tenant(tenant_id: str) -> list[dict]:
    return list(tax_invoices_container.query_items(
        query="SELECT c.id, c.number, c.issue_date, c.description, c.total, c.currency, c.payment_ref "
              "FROM c WHERE c.tenant_id = @tid ORDER BY c.created_at DESC",
        parameters=[{"name": "@tid", "value": tenant_id}],
        partition_key=tenant_id,
    ))
