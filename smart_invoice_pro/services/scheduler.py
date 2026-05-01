"""
Background scheduler for recurring invoice generation
Uses APScheduler to run daily and generate invoices from active recurring profiles
"""
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import uuid
import logging
from smart_invoice_pro.services.reminder_job import process_payment_reminders

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_recurring_profiles():
    """
    Process active recurring profiles and generate invoices
    This function is called by the scheduler
    """
    try:
        from smart_invoice_pro.utils.cosmos_client import recurring_profiles_container, invoices_container
        from smart_invoice_pro.api.recurring_profiles_api import calculate_next_run_date
        
        logger.info("Starting recurring invoice generation job...")
        
        today = datetime.utcnow().date().isoformat()
        
        # Query active profiles where next_run_date <= today
        query = f"SELECT * FROM c WHERE c.status = 'Active' AND c.next_run_date <= '{today}'"
        
        profiles = list(recurring_profiles_container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        logger.info(f"Found {len(profiles)} profiles to process")
        
        for profile in profiles:
            try:
                # Check if we've reached the occurrence limit
                if profile.get('occurrence_limit'):
                    if profile.get('occurrences_created', 0) >= profile['occurrence_limit']:
                        # Mark as expired
                        profile['status'] = 'Expired'
                        profile['updated_at'] = datetime.utcnow().isoformat()
                        recurring_profiles_container.replace_item(
                            item=profile['id'],
                            body=profile
                        )
                        logger.info(f"Profile {profile['id']} marked as expired (reached occurrence limit)")
                        continue
                
                # Check if we've passed the end date
                if profile.get('end_date'):
                    end_date = datetime.fromisoformat(profile['end_date'].replace('Z', '+00:00')).date()
                    if datetime.utcnow().date() > end_date:
                        profile['status'] = 'Expired'
                        profile['updated_at'] = datetime.utcnow().isoformat()
                        recurring_profiles_container.replace_item(
                            item=profile['id'],
                            body=profile
                        )
                        logger.info(f"Profile {profile['id']} marked as expired (passed end date)")
                        continue
                
                # Get next invoice number
                query_invoice = "SELECT * FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT 1"
                last_invoices = list(invoices_container.query_items(
                    query=query_invoice,
                    enable_cross_partition_query=True
                ))
                
                if last_invoices:
                    last_number = last_invoices[0].get('invoice_number', 'INV-000')
                    try:
                        prefix = 'INV-'
                        if last_number.startswith(prefix):
                            number = int(last_number.replace(prefix, ''))
                            next_invoice_number = f"{prefix}{str(number + 1).zfill(3)}"
                        else:
                            next_invoice_number = "INV-001"
                    except:
                        next_invoice_number = "INV-001"
                else:
                    next_invoice_number = "INV-001"
                
                # Calculate totals from items
                subtotal = 0
                items = profile.get('items', [])
                for item in items:
                    item_total = (item.get('quantity', 0) * item.get('rate', 0) - item.get('discount', 0))
                    subtotal += item_total
                
                total_tax = profile.get('cgst_amount', 0) + profile.get('sgst_amount', 0) + profile.get('igst_amount', 0)
                total_amount = subtotal + total_tax
                
                # Calculate due date (e.g., 30 days from issue)
                issue_date = datetime.utcnow().date().isoformat()
                due_date_obj = datetime.utcnow().date()
                try:
                    # Add 30 days for due date
                    from datetime import timedelta
                    due_date_obj = due_date_obj + timedelta(days=30)
                except:
                    pass
                due_date = due_date_obj.isoformat()
                
                # Create the invoice
                now = datetime.utcnow().isoformat()
                invoice = {
                    'id': str(uuid.uuid4()),
                    'invoice_number': next_invoice_number,
                    'customer_id': profile['customer_id'],
                    'customer_name': profile.get('customer_name', ''),
                    'customer_email': profile.get('customer_email', ''),
                    'customer_phone': profile.get('customer_phone', ''),
                    'issue_date': issue_date,
                    'due_date': due_date,
                    'payment_terms': profile.get('payment_terms', 'Net 30'),
                    'subtotal': subtotal,
                    'cgst_amount': profile.get('cgst_amount', 0.0),
                    'sgst_amount': profile.get('sgst_amount', 0.0),
                    'igst_amount': profile.get('igst_amount', 0.0),
                    'total_tax': total_tax,
                    'total_amount': total_amount,
                    'amount_paid': 0.0,
                    'balance_due': total_amount,
                    'status': 'Draft',
                    'payment_mode': '',
                    'notes': profile.get('notes', ''),
                    'terms_conditions': profile.get('terms_conditions', ''),
                    'is_gst_applicable': profile.get('is_gst_applicable', False),
                    'invoice_type': 'Tax Invoice',
                    'subject': f"Recurring Invoice - {profile.get('profile_name', '')}",
                    'salesperson': '',
                    'items': items,
                    'recurring_profile_id': profile['id'],
                    'created_at': now,
                    'updated_at': now
                }
                
                # Save the invoice
                created_invoice = invoices_container.create_item(body=invoice)
                logger.info(f"Created invoice {created_invoice['invoice_number']} from profile {profile['id']}")
                
                # TODO: Send email if email_reminder is True
                # This would integrate with an email service
                
                # Update the profile
                profile['last_run_date'] = today
                profile['next_run_date'] = calculate_next_run_date(
                    today,
                    profile.get('frequency'),
                    profile.get('recurrence_rule') or profile,
                )
                profile['occurrences_created'] = profile.get('occurrences_created', 0) + 1
                profile['updated_at'] = datetime.utcnow().isoformat()
                
                recurring_profiles_container.replace_item(
                    item=profile['id'],
                    body=profile
                )
                
                logger.info(f"Updated profile {profile['id']}, next run: {profile['next_run_date']}")
                
            except Exception as e:
                logger.error(f"Error processing profile {profile.get('id', 'unknown')}: {str(e)}")
                continue
        
        logger.info("Recurring invoice generation job completed")
        
    except Exception as e:
        logger.error(f"Error in recurring invoice generation job: {str(e)}")

def start_scheduler(app):
    """
    Initialize and start the background scheduler
    This should be called when the Flask app starts
    """
    scheduler = BackgroundScheduler()
    
    # Schedule the job to run daily at 00:05 (5 minutes past midnight)
    scheduler.add_job(
        func=process_recurring_profiles,
        trigger='cron',
        hour=0,
        minute=5,
        id='recurring_invoice_job',
        name='Generate Recurring Invoices',
        replace_existing=True
    )

    # Daily payment reminder job — runs at 09:05 AM
    scheduler.add_job(
        func=process_payment_reminders,
        trigger='cron',
        hour=9,
        minute=5,
        id='payment_reminder_job',
        name='Send Payment Reminders',
        replace_existing=True
    )
    
    # For testing, you can also add a job that runs more frequently
    # Uncomment this to run every 5 minutes for testing:
    # scheduler.add_job(
    #     func=process_recurring_profiles,
    #     trigger='interval',
    #     minutes=5,
    #     id='recurring_invoice_test_job',
    #     name='Test Recurring Invoices (Every 5 min)',
    #     replace_existing=True
    # )
    
    scheduler.start()
    logger.info("Background scheduler started successfully")
    
    # Store scheduler in app context for cleanup on shutdown
    app.scheduler = scheduler
    
    return scheduler

def shutdown_scheduler(app):
    """
    Gracefully shutdown the scheduler
    """
    if hasattr(app, 'scheduler'):
        app.scheduler.shutdown()
        logger.info("Background scheduler shut down successfully")
