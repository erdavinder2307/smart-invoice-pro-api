from flask import Blueprint, jsonify
from smart_invoice_pro.utils.cosmos_client import get_container
from smart_invoice_pro.utils.notifications import create_notification
from datetime import datetime
from flasgger import swag_from
from azure.communication.email import EmailClient
import os

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
                <em>This is an automated alert from Smart Invoice Pro Inventory Management System</em>
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
                        'SMTP_SERVER': 'smtp.gmail.com (default)',
                        'SMTP_PORT': '587 (default)',
                        'SMTP_USER': 'Your email address',
                        'SMTP_PASSWORD': 'Your email password or app-specific password',
                        'ALERT_EMAIL': 'Email to receive alerts (defaults to SMTP_USER)'
                    },
                    'scheduler_options': [
                        'Azure Functions Timer Trigger',
                        'AWS Lambda with EventBridge',
                        'Python APScheduler',
                        'External cron service (e.g., cron-job.org)'
                    ]
                }
            }
        ]
    })
