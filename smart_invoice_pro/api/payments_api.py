"""
Zoho Payments integration:
  POST /api/payments/create-session  – create a Zoho payment link and return the URL
  POST /api/payments/webhook         – handle Zoho webhook events (mark invoice Paid)
  GET  /api/payments/transactions    – list payment transactions for a user
  GET  /api/payments/status/<id>     – check status of a specific transaction

Required environment variables:
  ZOHO_PAYMENTS_CLIENT_ID
  ZOHO_PAYMENTS_CLIENT_SECRET
  ZOHO_PAYMENTS_REFRESH_TOKEN
  ZOHO_PAYMENTS_ACCOUNT_ID
  ZOHO_PAYMENTS_WEBHOOK_SECRET   (optional, for HMAC signature verification)
  FRONTEND_URL                   (e.g. http://localhost:3000)
"""

from flask import Blueprint, request, jsonify
import os, uuid, hmac, hashlib, requests
from datetime import datetime
from dotenv import load_dotenv
from smart_invoice_pro.utils.cosmos_client import invoices_container, get_container

load_dotenv()

payments_blueprint = Blueprint('payments', __name__)

# ── Cosmos container for payment transactions ─────────────────────────────────
payments_container = get_container("payments", "/user_id")

# ── Zoho OAuth helpers ────────────────────────────────────────────────────────
ZOHO_ACCOUNTS_URL = "https://accounts.zoho.com/oauth/v2/token"
ZOHO_PAYMENTS_BASE = "https://payments.zoho.com/api/v1"

def _get_zoho_access_token():
    """Exchange refresh_token for a short-lived access token."""
    resp = requests.post(ZOHO_ACCOUNTS_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     os.getenv("ZOHO_PAYMENTS_CLIENT_ID"),
        "client_secret": os.getenv("ZOHO_PAYMENTS_CLIENT_SECRET"),
        "refresh_token": os.getenv("ZOHO_PAYMENTS_REFRESH_TOKEN"),
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Zoho token error: {data}")
    return data["access_token"]


def _zoho_headers():
    return {
        "Authorization": f"Zoho-oauthtoken {_get_zoho_access_token()}",
        "Content-Type":  "application/json",
    }


# ── 1. Create payment session (payment link) ──────────────────────────────────
@payments_blueprint.route('/payments/create-session', methods=['POST'])
def create_payment_session():
    """
    Create a Zoho Payments link for an invoice and return the checkout URL.
    ---
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: [invoice_id, user_id]
          properties:
            invoice_id: {type: string}
            user_id:    {type: string}
    responses:
      200:
        description: Payment URL and transaction id
      400:
        description: Missing parameters
      404:
        description: Invoice not found
    """
    body = request.json or {}
    invoice_id = body.get("invoice_id")
    user_id    = body.get("user_id")

    if not invoice_id or not user_id:
        return jsonify({"error": "invoice_id and user_id are required"}), 400

    # Fetch invoice from Cosmos
    try:
        query = f"SELECT * FROM c WHERE c.id = '{invoice_id}'"
        items = list(invoices_container.query_items(query=query, enable_cross_partition_query=True))
        if not items:
            return jsonify({"error": "Invoice not found"}), 404
        invoice = items[0]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Guard: already paid
    if invoice.get("status") == "Paid":
        return jsonify({"error": "Invoice is already paid"}), 400

    balance_due  = float(invoice.get("balance_due") or invoice.get("total_amount", 0))
    currency     = invoice.get("currency", "INR")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Build Zoho payment-link payload
    payload = {
        "title":         f"Invoice {invoice.get('invoice_number', invoice_id)}",
        "description":   f"Payment for invoice {invoice.get('invoice_number', invoice_id)}",
        "amount":        balance_due,
        "currency":      currency,
        "reference_id":  invoice_id,
        "expiry_time":   72,                             # hours
        "redirect_url":  f"{frontend_url}/invoices?payment=success&invoice={invoice_id}",
        "cancel_url":    f"{frontend_url}/invoices?payment=cancelled&invoice={invoice_id}",
        "custom_fields": [
            {"label": "Invoice ID",  "value": invoice_id},
            {"label": "Customer ID", "value": str(invoice.get("customer_id", ""))},
            {"label": "User ID",     "value": user_id},
        ]
    }

    # Create the link via Zoho Payments API
    try:
        account_id = os.getenv("ZOHO_PAYMENTS_ACCOUNT_ID")
        zoho_resp  = requests.post(
            f"{ZOHO_PAYMENTS_BASE}/paymentlinks",
            json=payload,
            headers=_zoho_headers(),
            timeout=15,
        )
        zoho_resp.raise_for_status()
        zoho_data = zoho_resp.json()
    except requests.HTTPError as e:
        return jsonify({"error": "Zoho Payments API error", "details": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    payment_link_id  = zoho_data.get("payment_link_id") or zoho_data.get("id")
    payment_url      = zoho_data.get("short_url") or zoho_data.get("link_url") or zoho_data.get("url")

    # Persist pending transaction in Cosmos
    transaction = {
        "id":              str(uuid.uuid4()),
        "user_id":         user_id,
        "invoice_id":      invoice_id,
        "invoice_number":  invoice.get("invoice_number"),
        "amount":          balance_due,
        "currency":        currency,
        "status":          "pending",
        "payment_provider":"zoho_payments",
        "payment_link_id": payment_link_id,
        "payment_url":     payment_url,
        "created_at":      datetime.utcnow().isoformat(),
        "updated_at":      datetime.utcnow().isoformat(),
    }
    payments_container.create_item(body=transaction)

    return jsonify({
        "payment_url":      payment_url,
        "payment_link_id":  payment_link_id,
        "transaction_id":   transaction["id"],
        "amount":           balance_due,
        "currency":         currency,
    })


# ── 2. Webhook handler ────────────────────────────────────────────────────────
@payments_blueprint.route('/payments/webhook', methods=['POST'])
def zoho_payments_webhook():
    """
    Receive Zoho Payments webhook events and update invoice/transaction status.
    ---
    responses:
      200:
        description: Webhook processed
    """
    # Optional HMAC signature verification
    webhook_secret = os.getenv("ZOHO_PAYMENTS_WEBHOOK_SECRET")
    if webhook_secret:
        sig_header = request.headers.get("X-Zoho-Payments-Signature", "")
        body_bytes  = request.get_data()
        expected    = hmac.new(
            webhook_secret.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            return jsonify({"error": "Invalid signature"}), 401

    event = request.json or {}
    event_type = event.get("event_type") or event.get("type")
    data       = event.get("data") or event.get("payload") or {}

    if event_type in ("payment.success", "payment_link.paid", "payment.completed"):
        reference_id     = (
            data.get("reference_id") or
            data.get("custom_reference") or
            data.get("payment_link", {}).get("reference_id")
        )
        payment_link_id  = data.get("payment_link_id") or data.get("id")
        amount_paid      = float(data.get("amount") or data.get("amount_paid") or 0)
        zoho_txn_id      = data.get("transaction_id") or data.get("payment_id")

        # Update matching pending transaction
        try:
            txn_query = (
                f"SELECT * FROM c WHERE c.payment_link_id = '{payment_link_id}'"
                if payment_link_id else
                f"SELECT * FROM c WHERE c.invoice_id = '{reference_id}' AND c.status = 'pending'"
            )
            txns = list(payments_container.query_items(query=txn_query, enable_cross_partition_query=True))
            for txn in txns:
                txn["status"]           = "paid"
                txn["zoho_txn_id"]      = zoho_txn_id
                txn["amount_received"]  = amount_paid
                txn["paid_at"]          = datetime.utcnow().isoformat()
                txn["updated_at"]       = datetime.utcnow().isoformat()
                payments_container.replace_item(item=txn["id"], body=txn)
        except Exception as e:
            print(f"[Payments] Failed to update transaction: {e}")

        # Mark invoice as Paid
        if reference_id:
            try:
                inv_items = list(invoices_container.query_items(
                    query=f"SELECT * FROM c WHERE c.id = '{reference_id}'",
                    enable_cross_partition_query=True
                ))
                if inv_items:
                    inv = inv_items[0]
                    inv["status"]      = "Paid"
                    inv["amount_paid"] = amount_paid
                    inv["balance_due"] = max(0.0, float(inv.get("total_amount", 0)) - amount_paid)
                    inv["payment_mode"]= "Zoho Payments (Online)"
                    inv["updated_at"]  = datetime.utcnow().isoformat()
                    invoices_container.replace_item(
                        item=inv["id"], body=inv,
                        partition_key=inv.get("customer_id")
                    )
            except Exception as e:
                print(f"[Payments] Failed to update invoice: {e}")

    return jsonify({"status": "ok"})


# ── 3. List transactions ──────────────────────────────────────────────────────
@payments_blueprint.route('/payments/transactions', methods=['GET'])
def list_transactions():
    """
    List all payment transactions for a given user.
    ---
    parameters:
      - name: user_id
        in: query
        required: true
        type: string
    responses:
      200:
        description: List of transactions
    """
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    try:
        txns = list(payments_container.query_items(
            query=f"SELECT * FROM c WHERE c.user_id = '{user_id}' ORDER BY c.created_at DESC",
            enable_cross_partition_query=True
        ))
        return jsonify({"transactions": txns, "count": len(txns)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 4. Check transaction status ───────────────────────────────────────────────
@payments_blueprint.route('/payments/status/<transaction_id>', methods=['GET'])
def payment_status(transaction_id):
    """
    Check status of a specific payment transaction.
    ---
    parameters:
      - name: transaction_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Transaction status
      404:
        description: Transaction not found
    """
    try:
        txns = list(payments_container.query_items(
            query=f"SELECT * FROM c WHERE c.id = '{transaction_id}'",
            enable_cross_partition_query=True
        ))
        if not txns:
            return jsonify({"error": "Transaction not found"}), 404
        return jsonify(txns[0])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
