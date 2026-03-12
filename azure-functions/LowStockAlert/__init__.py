import datetime
import logging
import azure.functions as func
from azure.communication.email import EmailClient
from azure.cosmos import CosmosClient
import os


def main(mytimer: func.TimerRequest) -> None:
    """
    Azure Function Timer Trigger for Low Stock Alerts
    Runs daily at 9:00 AM to check inventory and send email alerts
    """
    utc_timestamp = datetime.datetime.utcnow().replace(
        tzinfo=datetime.timezone.utc).isoformat()

    if mytimer.past_due:
        logging.info('The timer is past due!')

    logging.info('Low Stock Alert function executed at %s', utc_timestamp)

    try:
        # Get environment variables
        connection_string = os.getenv('AZURE_EMAIL_CONNECTION_STRING')
        cosmos_uri = os.getenv('COSMOS_URI')
        cosmos_key = os.getenv('COSMOS_KEY')
        cosmos_db_name = os.getenv('COSMOS_DB_NAME')
        sender_email = os.getenv('SENDER_EMAIL', 'admin@solidevelectrosoft.com')
        alert_email = os.getenv('ALERT_EMAIL', 'davinder@solidevelectrosoft.com')

        if not all([connection_string, cosmos_uri, cosmos_key, cosmos_db_name]):
            logging.error('Missing required environment variables')
            return

        # Initialize Cosmos DB client
        cosmos_client = CosmosClient(cosmos_uri, cosmos_key)
        database = cosmos_client.get_database_client(cosmos_db_name)
        products_container = database.get_container_client('products')

        # Query for low stock products
        query = """
            SELECT p.id, p.name, p.availableQty, p.reorder_level, p.reorder_qty, p.unit
            FROM products p
            WHERE p.reorder_level > 0 
            AND p.availableQty <= p.reorder_level
            ORDER BY p.availableQty ASC
        """

        low_stock_products = list(products_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))

        logging.info(f'Found {len(low_stock_products)} low stock products')

        if not low_stock_products:
            logging.info('No low stock items found. Email not sent.')
            return

        # Send email alert
        email_client = EmailClient.from_connection_string(connection_string)

        subject = f'Low Stock Alert - {len(low_stock_products)} Products Need Restocking'

        html_content = """
        <html>
        <head>
            <style>
                body { font-family: Arial, sans-serif; color: #333; }
                table { border-collapse: collapse; width: 100%; margin: 20px 0; }
                th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
                th { background-color: #4CAF50; color: white; font-weight: bold; }
                .low { background-color: #fff3cd; }
                .critical { background-color: #f8d7da; }
                h1 { color: #333; }
                .footer { color: #999; font-size: 12px; margin-top: 30px; font-style: italic; }
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
            current = product.get('availableQty', 0)
            reorder = product.get('reorder_level', 0)
            reorder_qty = product.get('reorder_qty', 0)
            unit = product.get('unit', '')
            status_class = 'critical' if current == 0 else 'low'
            status_text = '🔴 OUT OF STOCK' if current == 0 else '⚠️ Low Stock'

            html_content += f"""
                <tr class="{status_class}">
                    <td><strong>{product['name']}</strong></td>
                    <td>{current:.2f} {unit}</td>
                    <td>{reorder:.2f}</td>
                    <td>{reorder_qty:.2f}</td>
                    <td><strong>{status_text}</strong></td>
                </tr>
            """

        html_content += """
                </tbody>
            </table>
            <p><strong>Action Required:</strong> Please review and create purchase orders for these items.</p>
            <p class="footer">This is an automated alert from Smart Invoice Pro Inventory Management System</p>
        </body>
        </html>
        """

        # Send email
        email_message = {
            "senderAddress": sender_email,
            "recipients": {
                "to": [{"address": alert_email}],
            },
            "content": {
                "subject": subject,
                "html": html_content
            }
        }

        poller = email_client.begin_send(email_message)
        result = poller.result()

        logging.info(f'Low stock alert email sent successfully. Message ID: {result["id"]}')

    except Exception as e:
        logging.error(f'Error in Low Stock Alert function: {str(e)}')
        raise
