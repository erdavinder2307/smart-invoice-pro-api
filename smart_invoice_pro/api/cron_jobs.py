from flask import Blueprint, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container, recurring_profiles_container, invoices_container
from smart_invoice_pro.utils.notifications import create_notification
from smart_invoice_pro.utils.audit_logger import log_audit
from datetime import datetime, date
from flasgger import swag_from
from azure.communication.email import EmailClient
import os
import uuid
import secrets

cron_blueprint = Blueprint('cron', __name__)

# Azure Communication Services configuration
CONNECTION_STRING = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
SENDER_ADDRESS = os.getenv('SENDER_EMAIL', "admin@solidevelectrosoft.com")
ALERT_EMAIL = os.getenv('ALERT_EMAIL', "davinder@solidevelectrosoft.com")

def send_low_stock_email(low_stock_products):
    """Send email alert for low stock products using Azure Communication Services"""
    try:
        if not CONNECTION_STRING:
            print("WARNING: AZURE_EMAIL_CONNECTION_STRING not configured. Email will NOT be sent.")
            return False
        
        client = EmailClient.from_connection_string(CONNECTION_STRING)
        
        # Create email content
        subject = f'Low Stock Alert - {len(low_stock_products)} Products Need Restocking'
        
        html_content = """
        <html>
        <head>
            <style>
                table { border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th { background-color: #4CAF50; color: white; font-weight: bold; }
                .low { background-color: #fff3cd; }
                .critical { background-color: #f8d7da; }
                h1 { color: #333; font-family: Arial, sans-serif; }
                p { font-family: Arial, sans-serif; color: #666; }
            </style>
        </head>
        <body>
            <h1>📦 Low Stock Alert</h1>
            <p>The following products are at or below their reorder levels and need restocking:</p>
            <table>
                <thead>
                    <tr>
                        <th>Product Name</th>
                        <th>Current Stock</th>
                        <th>Reorder Level</th>
                        <th>Recommended Order Qty</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for product in low_stock_products:
            current = product['current_stock']
            reorder = product['reorder_level']
            status_class = 'critical' if current == 0 else 'low'
            status_text = '🔴 OUT OF STOCK' if current == 0 else '⚠️ Low Stock'
            
            html_content += f"""
                <tr class="{status_class}">
                    <td><strong>{product['name']}</strong></td>
                    <td>{current:.2f} {product.get('unit', '')}</td>
                    <td>{reorder:.2f}</td>
                    <td>{product['reorder_qty']:.2f}</td>
                    <td><strong>{status_text}</strong></td>
                </tr>
            """
        
        html_content += """
                </tbody>
            </table>
            <br>
            <p><strong>Action Required:</strong> Please review and create purchase orders for these items.</p>
            <p style="color: #999; font-size: 12px; margin-top: 30px;">
                <em>This is an automated alert from Solidev Books Inventory Management System</em>
            </p>
        </body>
        </html>
        """
        
        # Send email using Azure Communication Services
        email_message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": {
                "to": [{"address": ALERT_EMAIL}],
            },
            "content": {
                "subject": subject,
                "html": html_content
            }
        }
        
        poller = client.begin_send(email_message)
        result = poller.result()
        
        print(f"Low stock alert email sent successfully. Message ID: {result['id']}")
        return True
        
    except Exception as e:
        print(f"Error sending email via Azure Communication Services: {str(e)}")
        return False

@cron_blueprint.route('/cron/check-low-stock', methods=['GET', 'POST'])
@swag_from({
    'tags': ['Cron Jobs'],
    'parameters': [
        {
            'name': 'send_email',
            'in': 'query',
            'type': 'boolean',
            'default': True,
            'description': 'Whether to send email alert'
        }
    ],
    'responses': {
        '200': {
            'description': 'Low stock check completed',
            'examples': {
                'application/json': {
                    'message': 'Low stock check completed',
                    'low_stock_count': 3,
                    'products': [],
                    'email_sent': True,
                    'timestamp': '2026-02-28T12:00:00Z'
                }
            }
        }
    }
})
def check_low_stock():
    """
    Cron job endpoint to check for low stock products and optionally send email alerts.
    Can be called by a scheduler (e.g., Azure Functions, AWS Lambda, or APScheduler).
    """
    try:
        from flask import request
        send_email_param = request.args.get('send_email', 'true').lower() == 'true'
        
        products_container = get_container("products", "/product_id")
        stock_container = get_container("stock", "/product_id")
        
        products = list(products_container.read_all_items())
        stock_transactions = list(stock_container.read_all_items())
        
        # Calculate current stock for each product
        stock_map = {}
        for txn in stock_transactions:
            pid = txn.get('product_id')
            qty = float(txn.get('quantity', 0))
            if pid not in stock_map:
                stock_map[pid] = 0.0
            if txn.get('type') == 'IN':
                stock_map[pid] += qty
            elif txn.get('type') == 'OUT':
                stock_map[pid] -= qty
        
        # Find products with low stock
        low_stock_products = []
        for product in products:
            pid = product.get('id')
            current_stock = stock_map.get(pid, 0.0)
            reorder_level = float(product.get('reorder_level', 0))
            
            if reorder_level > 0 and current_stock <= reorder_level:
                low_stock_products.append({
                    'id': pid,
                    'name': product.get('name', ''),
                    'category': product.get('category', ''),
                    'unit': product.get('unit', ''),
                    'current_stock': current_stock,
                    'reorder_level': reorder_level,
                    'reorder_qty': float(product.get('reorder_qty', 0)),
                    'preferred_vendor_id': product.get('preferred_vendor_id', '')
                })
        
        # Send email if there are low stock products
        email_sent = False
        if low_stock_products and send_email_param:
            email_sent = send_low_stock_email(low_stock_products)

        # Create per-product low-stock notifications (grouped by tenant)
        for lsp in low_stock_products:
            product_full = next(
                (p for p in products if p.get('id') == lsp['id']), {}
            )
            tid = product_full.get('tenant_id')
            if tid:
                create_notification(
                    tenant_id=tid,
                    notification_type='low_stock',
                    title='Low Stock Alert',
                    message=(
                        f"{lsp['name']} is running low: "
                        f"{lsp['current_stock']} {lsp['unit']} remaining "
                        f"(reorder level: {lsp['reorder_level']})."
                    ),
                    entity_id=lsp['id'],
                    entity_type='product',
                )
        
        return jsonify({
            'message': 'Low stock check completed',
            'low_stock_count': len(low_stock_products),
            'products': low_stock_products,
            'email_sent': email_sent,
            'timestamp': datetime.utcnow().isoformat()
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': f'Error checking low stock: {str(e)}',
            'timestamp': datetime.utcnow().isoformat()
        }), 500

@cron_blueprint.route('/cron/generate-recurring', methods=['POST'])
def generate_recurring_invoices():
    """
    Cron job endpoint: generate invoices from all Active recurring profiles whose
    next_run_date is today or in the past.  Call this daily from a scheduler.
    """
    from smart_invoice_pro.api.recurring_profiles_api import calculate_next_run_date
    from smart_invoice_pro.api.invoice_preferences_api import generate_invoice_number

    today_str = date.today().isoformat()
    now       = datetime.utcnow().isoformat()

    try:
        query = (
            "SELECT * FROM c "
            "WHERE c.status = 'Active' "
            "AND c.next_run_date <= @today"
        )
        params = [{'name': '@today', 'value': today_str}]
        due_profiles = list(recurring_profiles_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))
    except Exception as e:
        return jsonify({'error': f'Failed to query recurring profiles: {str(e)}'}), 500

    generated = []
    errors     = []

    for profile in due_profiles:
        tenant_id  = profile.get('tenant_id')
        profile_id = profile.get('id')

        try:
            recurrence_rule      = profile.get('recurrence_rule') or {}
            frequency            = profile.get('frequency', 'Monthly')
            occurrence_limit     = profile.get('occurrence_limit')
            occurrences_created  = int(profile.get('occurrences_created', 0))
            ends_type            = profile.get('ends_type', 'never')
            end_date_str         = profile.get('end_date')

            # Skip if profile has reached occurrence limit
            if ends_type == 'after_occurrences' and occurrence_limit is not None:
                if occurrences_created >= int(occurrence_limit):
                    profile['status']     = 'Completed'
                    profile['updated_at'] = now
                    recurring_profiles_container.replace_item(item=profile_id, body=profile)
                    continue

            # Skip if profile's end date has passed
            if ends_type == 'on_date' and end_date_str and today_str > end_date_str:
                profile['status']     = 'Completed'
                profile['updated_at'] = now
                recurring_profiles_container.replace_item(item=profile_id, body=profile)
                continue

            # Build invoice document from the profile template
            items        = profile.get('items', [])
            subtotal     = sum(
                float(i.get('quantity', 0)) * float(i.get('unit_price', i.get('price', 0)))
                for i in items
            )
            cgst_amount  = float(profile.get('cgst_amount', 0.0))
            sgst_amount  = float(profile.get('sgst_amount', 0.0))
            igst_amount  = float(profile.get('igst_amount', 0.0))
            total_tax    = cgst_amount + sgst_amount + igst_amount
            total_amount = round(subtotal + total_tax, 2)

            invoice_number = generate_invoice_number(tenant_id)
            invoice_status = 'Issued' if profile.get('auto_send') else 'Draft'

            invoice = {
                'id':                  str(uuid.uuid4()),
                'invoice_number':      invoice_number,
                'customer_id':         profile.get('customer_id'),
                'customer_name':       profile.get('customer_name', ''),
                'issue_date':          today_str,
                'due_date':            today_str,
                'payment_terms':       profile.get('payment_terms', ''),
                'subtotal':            round(subtotal, 2),
                'cgst_amount':         cgst_amount,
                'sgst_amount':         sgst_amount,
                'igst_amount':         igst_amount,
                'total_tax':           total_tax,
                'total_amount':        total_amount,
                'amount_paid':         0.0,
                'balance_due':         total_amount,
                'invoice_discount':    0.0,
                'round_off':           0.0,
                'status':              invoice_status,
                'lifecycle_status':    'ACTIVE',
                'payment_mode':        '',
                'notes':               profile.get('notes', ''),
                'terms_conditions':    profile.get('terms_conditions', ''),
                'is_gst_applicable':   bool(profile.get('is_gst_applicable', False)),
                'gst_treatment':       'regular',
                'invoice_type':        'recurring',
                'recurring_profile_id': profile_id,
                'items':               items,
                'tenant_id':           tenant_id,
                'portal_token':        secrets.token_urlsafe(32),
                'created_at':          now,
                'updated_at':          now,
            }

            invoices_container.create_item(body=invoice)

            log_audit(
                'invoice', 'create', invoice['id'], None, invoice,
                user_id='cron', tenant_id=tenant_id,
            )
            create_notification(
                tenant_id=tenant_id,
                notification_type='recurring_invoice_generated',
                title='Recurring Invoice Generated',
                message=(
                    f"Invoice {invoice_number} for {profile.get('customer_name', '')} "
                    f"(₹{total_amount:,.2f}) was auto-generated from recurring profile "
                    f"'{profile.get('profile_name', profile_id)}'."
                ),
                entity_id=invoice['id'],
                entity_type='invoice',
                user_id='cron',
            )

            # Advance the profile: update last_run_date, next_run_date, occurrences_created
            new_next_run = calculate_next_run_date(today_str, frequency, recurrence_rule)
            new_occurrences = occurrences_created + 1
            profile['last_run_date']        = today_str
            profile['next_run_date']        = new_next_run
            profile['occurrences_created']  = new_occurrences
            profile['updated_at']           = now

            # Mark Completed if this was the final occurrence
            if ends_type == 'after_occurrences' and occurrence_limit is not None:
                if new_occurrences >= int(occurrence_limit):
                    profile['status'] = 'Completed'

            recurring_profiles_container.replace_item(item=profile_id, body=profile)
            generated.append({'profile_id': profile_id, 'invoice_id': invoice['id'],
                               'invoice_number': invoice_number})

        except Exception as e:
            errors.append({'profile_id': profile_id, 'error': str(e)})

    return jsonify({
        'message':        'Recurring invoice generation completed',
        'generated_count': len(generated),
        'error_count':     len(errors),
        'generated':       generated,
        'errors':          errors,
        'timestamp':       now,
    }), 200


@cron_blueprint.route('/cron/schedule-info', methods=['GET'])
@swag_from({
    'tags': ['Cron Jobs'],
    'responses': {
        '200': {
            'description': 'Information about scheduled cron jobs',
            'examples': {
                'application/json': {
                    'jobs': [
                        {
                            'name': 'Low Stock Check',
                            'endpoint': '/api/cron/check-low-stock',
                            'frequency': 'Daily at 9:00 AM',
                            'description': 'Checks inventory levels and sends email alerts for low stock items'
                        }
                    ]
                }
            }
        }
    }
})
def get_schedule_info():
    """Get information about configured cron jobs"""
    return jsonify({
        'jobs': [
            {
                'name': 'Low Stock Check',
                'endpoint': '/api/cron/check-low-stock',
                'recommended_frequency': 'Daily at 9:00 AM',
                'description': 'Checks inventory levels and sends email alerts for low stock items',
                'setup_instructions': {
                    'environment_variables': {
                        'AZURE_EMAIL_CONNECTION_STRING': 'Azure Communication Services connection string',
                        'SENDER_EMAIL': 'Sender address (default: admin@solidevelectrosoft.com)',
                        'ALERT_EMAIL': 'Email to receive low-stock alerts',
                    },
                    'scheduler_options': [
                        'Azure Functions Timer Trigger',
                        'AWS Lambda with EventBridge',
                        'Python APScheduler',
                        'External cron service (e.g., cron-job.org)'
                    ]
                }
            },
            {
                'name': 'Generate Recurring Invoices',
                'endpoint': '/api/cron/generate-recurring',
                'method': 'POST',
                'recommended_frequency': 'Daily at 6:00 AM',
                'description': (
                    'Creates Draft (or Issued if auto_send=true) invoices for all Active '
                    'recurring profiles whose next_run_date is today or earlier. '
                    'Advances next_run_date and marks profiles Completed when limits are reached.'
                ),
                'scheduler_options': [
                    'Azure Functions Timer Trigger',
                    'AWS Lambda with EventBridge',
                    'External cron service (e.g., cron-job.org)'
                ]
            },
        ]
    })
