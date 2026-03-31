"""
Payment reminder job — runs daily at 09:05 AM.

For each unpaid invoice (status Issued or Partially Paid, balance_due > 0):
  - Checks configured before-due and after-due offsets
  - Skips if reminder already sent for that offset (dedup via reminder_log)
  - Sends email via Azure Communication Services (same pattern as cron_jobs.py)
  - Appends entry to invoice.reminder_log and saves back to Cosmos DB
"""
import os
import logging
from datetime import datetime, date, timedelta

from azure.communication.email import EmailClient

logger = logging.getLogger(__name__)

CONNECTION_STRING = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
SENDER_ADDRESS    = os.getenv('SENDER_EMAIL', 'admin@solidevelectrosoft.com')

# Statuses that should receive reminders
REMINDER_STATUSES = {'Issued', 'Partially Paid'}


def _load_reminder_config(settings_container, tenant_id):
    """Return reminder config for tenant, falling back to sensible defaults."""
    doc_id = f"{tenant_id}:reminder_settings"
    items = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.id = @id AND c.tenant_id = @tid",
        parameters=[
            {"name": "@id",  "value": doc_id},
            {"name": "@tid", "value": tenant_id},
        ],
        enable_cross_partition_query=True
    ))
    if items:
        return items[0]
    return {
        'reminders_enabled': True,
        'before_due_days':   [3],
        'after_due_days':    [1, 3, 7],
    }


def _already_sent(invoice, reminder_type, days_offset):
    """Return True if this exact reminder was already sent."""
    log = invoice.get('reminder_log', [])
    return any(
        e.get('reminder_type') == reminder_type and e.get('days_offset') == days_offset
        for e in log
    )


def _send_reminder_email(invoice, days_label):
    """Send a payment reminder email using ACS.  Returns True on success."""
    recipient = invoice.get('customer_email', '').strip()
    if not recipient:
        logger.warning(f"Invoice {invoice.get('invoice_number')} — no customer email, skipping.")
        return False

    if not CONNECTION_STRING:
        logger.warning("AZURE_EMAIL_CONNECTION_STRING not set. Email NOT sent.")
        return False

    invoice_number = invoice.get('invoice_number', invoice.get('id', 'N/A'))
    customer_name  = invoice.get('customer_name', 'Valued Customer')
    total_amount   = invoice.get('total_amount', 0)
    balance_due    = invoice.get('balance_due', total_amount)
    due_date       = invoice.get('due_date', 'N/A')

    subject = f"Payment Reminder: Invoice {invoice_number}"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
      <div style="max-width:600px; margin:auto; border:1px solid #e0e0e0; border-radius:8px; overflow:hidden;">
        <div style="background:#1a73e8; padding:20px 28px;">
          <h2 style="color:#fff; margin:0;">Payment Reminder</h2>
        </div>
        <div style="padding:28px;">
          <p>Dear <strong>{customer_name}</strong>,</p>
          <p>This is a friendly reminder that the following invoice is {days_label}:</p>
          <table style="width:100%; border-collapse:collapse; margin:16px 0;">
            <tr style="background:#f5f7fa;">
              <td style="padding:10px 14px; font-weight:600; border:1px solid #e0e0e0;">Invoice #</td>
              <td style="padding:10px 14px; border:1px solid #e0e0e0;">{invoice_number}</td>
            </tr>
            <tr>
              <td style="padding:10px 14px; font-weight:600; border:1px solid #e0e0e0;">Due Date</td>
              <td style="padding:10px 14px; border:1px solid #e0e0e0;">{due_date}</td>
            </tr>
            <tr style="background:#f5f7fa;">
              <td style="padding:10px 14px; font-weight:600; border:1px solid #e0e0e0;">Invoice Total</td>
              <td style="padding:10px 14px; border:1px solid #e0e0e0;">&#8377;{total_amount:,.2f}</td>
            </tr>
            <tr style="background:#fff3cd;">
              <td style="padding:10px 14px; font-weight:700; border:1px solid #e0e0e0; color:#856404;">Balance Due</td>
              <td style="padding:10px 14px; font-weight:700; border:1px solid #e0e0e0; color:#856404;">&#8377;{balance_due:,.2f}</td>
            </tr>
          </table>
          <p>Please arrange payment at your earliest convenience to avoid any late fees.</p>
          <p style="color:#999; font-size:12px; margin-top:32px;">
            This is an automated reminder from Smart Invoice Pro.
          </p>
        </div>
      </div>
    </body>
    </html>
    """

    try:
        client = EmailClient.from_connection_string(CONNECTION_STRING)
        poller = client.begin_send({
            "senderAddress": SENDER_ADDRESS,
            "recipients": {"to": [{"address": recipient}]},
            "content": {"subject": subject, "html": html_body},
        })
        poller.result()
        logger.info(f"Reminder sent for invoice {invoice_number} to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reminder for invoice {invoice_number}: {e}")
        return False


def process_payment_reminders():
    """
    Scheduled job — evaluate every open invoice against the tenant's
    reminder config and send emails where needed.
    """
    try:
        from smart_invoice_pro.utils.cosmos_client import invoices_container, settings_container

        today = date.today()
        logger.info(f"[reminders] Starting payment reminder job for {today.isoformat()}")

        # Fetch all remindable invoices across all tenants
        invoices = list(invoices_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status IN ('Issued', 'Partially Paid') "
                "AND (c.balance_due > 0 OR NOT IS_DEFINED(c.balance_due))"
            ),
            enable_cross_partition_query=True
        ))

        logger.info(f"[reminders] Found {len(invoices)} open invoices to evaluate")

        # Group by tenant so we only load each tenant's config once
        by_tenant: dict[str, list] = {}
        for inv in invoices:
            tid = inv.get('tenant_id', '__none__')
            by_tenant.setdefault(tid, []).append(inv)

        sent_count = 0
        skipped_count = 0

        for tenant_id, tenant_invoices in by_tenant.items():
            cfg = _load_reminder_config(settings_container, tenant_id)

            if not cfg.get('reminders_enabled', True):
                logger.info(f"[reminders] Tenant {tenant_id}: reminders disabled, skipping.")
                continue

            before_days: list[int] = cfg.get('before_due_days', [])
            after_days:  list[int] = cfg.get('after_due_days', [])

            for inv in tenant_invoices:
                due_date_str = inv.get('due_date', '')
                if not due_date_str:
                    skipped_count += 1
                    continue

                try:
                    due = date.fromisoformat(due_date_str[:10])
                except ValueError:
                    skipped_count += 1
                    continue

                days_diff = (today - due).days  # negative = before due

                checks = [
                    *[('before_due', d, f"due in {d} day{'s' if d != 1 else ''}") for d in before_days],
                    *[('after_due',  d, f"overdue by {d} day{'s' if d != 1 else ''}") for d in after_days],
                ]

                for reminder_type, offset, label in checks:
                    expected_diff = offset if reminder_type == 'after_due' else -offset

                    if days_diff != expected_diff:
                        continue

                    if _already_sent(inv, reminder_type, offset):
                        logger.debug(
                            f"[reminders] Invoice {inv.get('invoice_number')} — "
                            f"{reminder_type}:{offset} already sent, skipping."
                        )
                        continue

                    ok = _send_reminder_email(inv, label)

                    log_entry = {
                        'reminder_type': reminder_type,
                        'days_offset':   offset,
                        'sent_at':       datetime.utcnow().isoformat(),
                        'email_sent':    ok,
                        'recipient':     inv.get('customer_email', ''),
                    }
                    inv.setdefault('reminder_log', []).append(log_entry)
                    inv['updated_at'] = datetime.utcnow().isoformat()

                    try:
                        invoices_container.replace_item(item=inv['id'], body=inv)
                    except Exception as e:
                        logger.error(f"[reminders] Failed to update invoice {inv.get('id')}: {e}")

                    if ok:
                        sent_count += 1
                    else:
                        skipped_count += 1

        logger.info(
            f"[reminders] Job complete — sent: {sent_count}, skipped/failed: {skipped_count}"
        )

    except Exception as e:
        logger.error(f"[reminders] Fatal error in reminder job: {e}")
