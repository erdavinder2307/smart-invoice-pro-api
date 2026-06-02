"""
AI-powered transaction reconciliation using Claude (Anthropic).

Provides `ai_match_transaction()` which accepts a bank transaction and a set of
candidate invoices / expenses, then asks Claude to select the best match and
return a structured confidence score + short reasoning.

Usage
-----
from smart_invoice_pro.services.ai_reconciliation_service import ai_match_transaction

suggestion = ai_match_transaction(txn_doc, candidate_invoices, candidate_expenses)
# → {"match_type": "invoice", "match_id": "inv-abc", "confidence": 0.92, "reasoning": "..."}

Environment variables
---------------------
ANTHROPIC_API_KEY   Required.  Your Anthropic API key.
CLAUDE_MODEL        Optional.  Defaults to "claude-3-5-haiku-20241022".
                               Use "claude-3-5-sonnet-20241022" for higher accuracy.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional


_SYSTEM_PROMPT = (
    "You are a financial transaction matching assistant for a small-business invoicing platform. "
    "Given a bank transaction and a list of candidate matches (unpaid invoices and recorded expenses), "
    "identify the single best match if one exists. "
    "Prefer invoice matches for credits (money received into the account) and expense matches for debits "
    "(money paid out). Match on payee/customer name in the description, amount proximity, and date proximity. "
    "Only return a match when you are reasonably confident (>=0.6). "
    "Respond ONLY with valid JSON — no markdown fences, no explanation outside the JSON object."
)


def _get_client():
    """Return a lazily-instantiated Anthropic client.

    Raises
    ------
    ImportError   if the `anthropic` package is not installed.
    RuntimeError  if ANTHROPIC_API_KEY is not set.
    """
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "The 'anthropic' package is required for AI reconciliation. "
            "Install it with: pip install anthropic"
        ) from exc

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Add it to your .env file or Azure App Service settings."
        )

    return anthropic.Anthropic(api_key=api_key)


def ai_match_transaction(
    txn: dict,
    candidate_invoices: list,
    candidate_expenses: list,
    model: Optional[str] = None,
) -> dict:
    """Ask Claude to match a bank transaction to the best candidate.

    Parameters
    ----------
    txn                 Bank transaction document with keys: date, description, amount.
    candidate_invoices  List of invoice dicts (id, invoice_number, customer_name,
                        balance_due, due_date).
    candidate_expenses  List of expense dicts (id, vendor_name, amount, date, category).
    model               Claude model name override.  Falls back to CLAUDE_MODEL env var,
                        then "claude-3-5-haiku-20241022".

    Returns
    -------
    dict with keys:
        match_type  : "invoice" | "expense" | None
        match_id    : str | None
        confidence  : float  (0.0 – 1.0)
        reasoning   : str

    Raises
    ------
    RuntimeError   if ANTHROPIC_API_KEY is missing.
    ValueError     if Claude returns JSON that cannot be parsed or has unexpected shape.
    """
    client = _get_client()
    model = model or os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    amount = txn.get("amount", 0)
    direction = "credit (money received)" if amount >= 0 else "debit (money paid out)"

    # Build candidate blocks (cap at 20 each to stay within token budget)
    inv_lines = []
    for i, inv in enumerate(candidate_invoices[:20], 1):
        inv_lines.append(
            f"{i}. ID: {inv.get('id')}"
            f", Invoice#: {inv.get('invoice_number', 'N/A')}"
            f", Customer: {inv.get('customer_name') or inv.get('customer_id', 'N/A')}"
            f", Balance Due: {float(inv.get('balance_due', 0)):.2f}"
            f", Due Date: {inv.get('due_date', 'N/A')}"
        )

    exp_lines = []
    for i, exp in enumerate(candidate_expenses[:20], 1):
        exp_lines.append(
            f"{i}. ID: {exp.get('id')}"
            f", Vendor: {exp.get('vendor_name', 'N/A')}"
            f", Amount: {float(exp.get('amount', 0)):.2f}"
            f", Date: {exp.get('date', 'N/A')}"
            f", Category: {exp.get('category', 'N/A')}"
        )

    inv_block = "\n".join(inv_lines) if inv_lines else "(none)"
    exp_block = "\n".join(exp_lines) if exp_lines else "(none)"

    user_message = (
        f"Match this bank transaction to the best candidate, if any.\n\n"
        f"Transaction:\n"
        f"  Date: {txn.get('date', 'unknown')}\n"
        f"  Description: \"{txn.get('description', '')}\"\n"
        f"  Amount: {abs(amount):.2f} ({direction})\n\n"
        f"Candidate Invoices (unpaid/overdue):\n{inv_block}\n\n"
        f"Candidate Expenses:\n{exp_block}\n\n"
        f"Rules:\n"
        f"  - Prefer invoice matches for credits; prefer expense matches for debits.\n"
        f"  - Match on payee/customer name similarity, amount closeness, and date proximity.\n"
        f"  - Return null values when no candidate is a strong enough match (confidence < 0.6).\n\n"
        f"Respond with this exact JSON structure:\n"
        f'{{\n'
        f'  "match_type": "invoice" | "expense" | null,\n'
        f'  "match_id": "<exact id from the candidate list above>" | null,\n'
        f'  "confidence": <float 0.0-1.0>,\n'
        f'  "reasoning": "<one concise sentence>"\n'
        f'}}'
    )

    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip any markdown code fences Claude might add despite instructions
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude returned invalid JSON: {raw!r}"
        ) from exc

    # Validate & normalise
    match_type = result.get("match_type")
    match_id = result.get("match_id")
    confidence = float(result.get("confidence", 0.0))

    if match_type not in ("invoice", "expense", None):
        match_type = None
        match_id = None
        confidence = 0.0

    # Sanity check: ensure the returned id actually came from our candidate list
    valid_ids = (
        {inv.get("id") for inv in candidate_invoices}
        | {exp.get("id") for exp in candidate_expenses}
    )
    if match_id and match_id not in valid_ids:
        match_type = None
        match_id = None
        confidence = 0.0

    return {
        "match_type": match_type,
        "match_id": match_id,
        "confidence": confidence,
        "reasoning": result.get("reasoning", ""),
    }
