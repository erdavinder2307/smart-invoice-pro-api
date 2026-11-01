"""
Trial lifecycle scheduler jobs.
Registered in smart_invoice_pro/services/scheduler.py alongside existing jobs.

Jobs:
  check_trial_expirations  — daily 07:00 UTC
    • Sends D-7 warning email
    • Sends D-1 warning email
    • Expires trial on D-0 + soft-suspends tenant

  send_activation_nudges   — weekly Monday 08:00 UTC
    • Sends Day-7 nudge to accounts with no invoice yet
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _send_email(tenant_id: str, template: str, context: dict):
    """Thin wrapper around Azure Communication Services email."""
    try:
        from smart_invoice_pro.utils.cosmos_client import users_container, tenants_container
        # Find admin user email for this tenant
        users = list(users_container.query_items(
            query="SELECT c.email, c.name FROM c WHERE c.tenant_id = @tid AND c.role = 'Admin' OFFSET 0 LIMIT 1",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        if not users:
            return
        admin = users[0]

        # Get tenant name
        tenants = list(tenants_container.query_items(
            query="SELECT c.name FROM c WHERE c.id = @tid OFFSET 0 LIMIT 1",
            parameters=[{"name": "@tid", "value": tenant_id}],
            enable_cross_partition_query=True,
        ))
        tenant_name = tenants[0]["name"] if tenants else "your organisation"

        _dispatch_email(admin["email"], admin.get("name", ""), tenant_name, template, context)
    except Exception as e:
        logger.error(f"[trial_scheduler] _send_email failed for {tenant_id}: {e}")


def _dispatch_email(to_email: str, to_name: str, tenant_name: str, template: str, context: dict):
    """Send email via Azure Communication Services."""
    import os
    sender = os.getenv("AZURE_SENDER_EMAIL", "")
    if not sender:
        logger.warning("[trial_scheduler] AZURE_SENDER_EMAIL not set — skipping email")
        return

    subjects = {
        "trial_ending_7d": f"7 days left in your Solidev Books trial",
        "trial_ending_1d": f"Your Solidev Books trial ends tomorrow",
        "trial_expired": f"Your Solidev Books trial has ended — upgrade to keep access",
        "extension_1_granted": f"Your trial has been extended by 30 days",
        "extension_2_approved": f"Your second trial extension is approved",
        "extension_2_denied": f"Your extension request was not approved",
        "founding_customer": f"Welcome, Founding Customer — you have extended access",
        "activation_nudge": f"Have you created your first invoice yet?",
    }

    bodies = {
        "trial_ending_7d": (
            f"Hi {to_name},\n\n"
            f"Your Solidev Books trial for {tenant_name} ends in 7 days "
            f"(on {context.get('trial_ends_at', '')}).\n\n"
            f"Upgrade to Starter at ₹599/month to keep all your data and continue working.\n\n"
            f"Upgrade now: {context.get('upgrade_url', '')}\n\n"
            f"— The Solidev Books team"
        ),
        "trial_ending_1d": (
            f"Hi {to_name},\n\n"
            f"Your Solidev Books trial for {tenant_name} ends tomorrow.\n\n"
            f"Don't lose access to your invoices, customers, and reports.\n\n"
            f"Upgrade to Starter at ₹599/month — takes 2 minutes:\n"
            f"{context.get('upgrade_url', '')}\n\n"
            f"Or request a 30-day extension:\n"
            f"{context.get('extension_url', '')}\n\n"
            f"— The Solidev Books team"
        ),
        "trial_expired": (
            f"Hi {to_name},\n\n"
            f"Your Solidev Books trial for {tenant_name} has ended.\n\n"
            f"Your data is safe — we keep it for 90 days. Upgrade at any time to restore full access.\n\n"
            f"Upgrade to Starter at ₹599/month:\n"
            f"{context.get('upgrade_url', '')}\n\n"
            f"— The Solidev Books team"
        ),
        "extension_1_granted": (
            f"Hi {to_name},\n\n"
            f"Good news! Your Solidev Books trial has been extended by 30 days.\n\n"
            f"Your new trial end date: {context.get('trial_ends_at', '')}\n\n"
            f"Use this time to explore all features and consider upgrading to Starter when ready.\n\n"
            f"— The Solidev Books team"
        ),
        "extension_2_approved": (
            f"Hi {to_name},\n\n"
            f"Your second trial extension request has been approved.\n\n"
            f"Your new trial end date: {context.get('trial_ends_at', '')}\n\n"
            f"— The Solidev Books team"
        ),
        "extension_2_denied": (
            f"Hi {to_name},\n\n"
            f"We were unable to approve your extension request at this time.\n\n"
            f"You can upgrade to Starter at ₹599/month to continue:\n"
            f"{context.get('upgrade_url', '')}\n\n"
            f"If you have questions, reply to this email.\n\n"
            f"— The Solidev Books team"
        ),
        "founding_customer": (
            f"Hi {to_name},\n\n"
            f"Welcome to the Solidev Books Founding Customer program!\n\n"
            f"You now have extended access until {context.get('trial_ends_at', '')}.\n\n"
            f"As a Founding Customer, you'll have direct input into our product roadmap "
            f"and locked-in pricing when you convert.\n\n"
            f"— The Solidev Books team"
        ),
        "activation_nudge": (
            f"Hi {to_name},\n\n"
            f"You signed up for Solidev Books a week ago — have you created your first invoice yet?\n\n"
            f"It takes less than 2 minutes. Here's how to get started:\n"
            f"1. Go to Invoices → Add Invoice\n"
            f"2. Select a customer (or add a new one)\n"
            f"3. Add your items and click Send\n\n"
            f"Log in now: {context.get('app_url', '')}\n\n"
            f"— The Solidev Books team"
        ),
    }

    subject = subjects.get(template, "Solidev Books — Account update")
    body = bodies.get(template, "")

    try:
        from azure.communication.email import EmailClient
        connection_string = os.getenv("AZURE_EMAIL_CONNECTION_STRING", "")
        if not connection_string:
            # Fallback: log email content for manual sending
            logger.info(f"[trial_scheduler] EMAIL TO {to_email}: {subject}\n{body}")
            return

        client = EmailClient.from_connection_string(connection_string)
        client.begin_send({
            "senderAddress": sender,
            "recipients": {"to": [{"address": to_email, "displayName": to_name}]},
            "content": {"subject": subject, "plainText": body},
        })
        logger.info(f"[trial_scheduler] Email sent: {template} → {to_email}")
    except ImportError:
        logger.info(f"[trial_scheduler] Azure email SDK not available — EMAIL TO {to_email}: {subject}")
    except Exception as e:
        logger.error(f"[trial_scheduler] Email dispatch failed: {e}")


def check_trial_expirations():
    """
    Daily job: scan all trial subscriptions and take action based on days remaining.
    Runs at 07:00 UTC — before business hours in IST (12:30 PM IST).
    """
    try:
        from smart_invoice_pro.utils.cosmos_client import subscriptions_container
        import os

        now = datetime.now(timezone.utc)
        frontend_url = os.getenv("FRONTEND_URL", "https://app.solidevbooks.com")
        upgrade_url = f"{frontend_url}/settings/billing/upgrade"
        extension_url = f"{frontend_url}/settings/billing"

        trial_statuses = ("trial", "trial_extension_1", "trial_extension_2", "founding_customer")
        status_filter = " OR ".join([f"c.status = '{s}'" for s in trial_statuses])

        subs = list(subscriptions_container.query_items(
            query=f"SELECT * FROM c WHERE ({status_filter})",
            enable_cross_partition_query=True,
        ))

        logger.info(f"[trial_scheduler] check_trial_expirations: found {len(subs)} active trial subscriptions")

        for sub in subs:
            tenant_id = sub.get("tenant_id")
            trial_ends_str = sub.get("trial_ends_at")
            if not trial_ends_str or not tenant_id:
                continue

            try:
                trial_ends = datetime.fromisoformat(trial_ends_str.replace("Z", "+00:00"))
            except Exception:
                continue

            days_remaining = (trial_ends - now).days

            if days_remaining == 7:
                _send_email(tenant_id, "trial_ending_7d", {
                    "trial_ends_at": trial_ends.strftime("%d %b %Y"),
                    "upgrade_url": upgrade_url,
                })

            elif days_remaining == 1:
                _send_email(tenant_id, "trial_ending_1d", {
                    "trial_ends_at": trial_ends.strftime("%d %b %Y"),
                    "upgrade_url": upgrade_url,
                    "extension_url": extension_url,
                })

            elif days_remaining <= 0 and sub.get("status") in trial_statuses:
                from smart_invoice_pro.services.subscription_service import expire_trial
                expire_trial(tenant_id)
                _send_email(tenant_id, "trial_expired", {"upgrade_url": upgrade_url})
                logger.info(f"[trial_scheduler] Expired trial for tenant {tenant_id}")

    except Exception as e:
        logger.error(f"[trial_scheduler] check_trial_expirations failed: {e}")


def send_activation_nudges():
    """
    Weekly job: send nudge emails to accounts that signed up ~7 days ago
    but haven't created their first invoice yet.
    """
    try:
        from smart_invoice_pro.utils.cosmos_client import subscriptions_container, invoices_container
        import os

        now = datetime.now(timezone.utc)
        week_ago_start = (now - timedelta(days=8)).isoformat()
        week_ago_end = (now - timedelta(days=6)).isoformat()
        frontend_url = os.getenv("FRONTEND_URL", "https://app.solidevbooks.com")

        # Tenants whose trial started ~7 days ago
        subs = list(subscriptions_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'trial' "
                "AND c.created_at >= @start AND c.created_at <= @end"
            ),
            parameters=[
                {"name": "@start", "value": week_ago_start},
                {"name": "@end", "value": week_ago_end},
            ],
            enable_cross_partition_query=True,
        ))

        logger.info(f"[trial_scheduler] send_activation_nudges: checking {len(subs)} week-old trials")

        for sub in subs:
            tenant_id = sub.get("tenant_id")
            if not tenant_id:
                continue

            # Check if they have any invoices
            inv_count = list(invoices_container.query_items(
                query="SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid",
                parameters=[{"name": "@tid", "value": tenant_id}],
                enable_cross_partition_query=True,
            ))
            if inv_count and inv_count[0] > 0:
                continue  # Already active — skip nudge

            _send_email(tenant_id, "activation_nudge", {"app_url": frontend_url})

    except Exception as e:
        logger.error(f"[trial_scheduler] send_activation_nudges failed: {e}")
