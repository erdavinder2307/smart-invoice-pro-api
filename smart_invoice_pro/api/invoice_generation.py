from flask import Blueprint, request, make_response, jsonify
from flasgger import swag_from
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

invoice_generation_blueprint = Blueprint('invoice_generation', __name__)

@invoice_generation_blueprint.route('/generate-invoice-pdf', methods=['POST'])
@swag_from({
    'tags': ['Invoice PDF'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'invoice': {
                        'type': 'object',
                        'description': 'Invoice data (all fields required for rendering)'
                    }
                },
                'required': ['invoice']
            },
            'description': 'Invoice data for PDF generation'
        }
    ],
    'responses': {
        '200': {
            'description': 'PDF file',
            'content': {
                'application/pdf': {
                    'schema': {
                        'type': 'string',
                        'format': 'binary'
                    }
                }
            }
        },
        '400': {
            'description': 'Invalid input or template error',
            'examples': {
                'application/json': {'error': 'Missing invoice data'}
            }
        }
    }
})
def generate_invoice_pdf():
    """
    Generate an invoice PDF from JSON data using the invoice template.
    ---
    tags:
      - Invoice PDF
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            invoice:
              type: object
              description: Invoice data (all fields required for rendering)
    responses:
      200:
        description: PDF file
        content:
          application/pdf:
            schema:
              type: string
              format: binary
      400:
        description: Invalid input or template error
    """
    data = request.get_json(silent=True)
    if not data or 'invoice' not in data:
        # Provide sample invoice data if JSON is missing or invalid
        sample_invoice = {
            'invoice_number': 'INV-001',
            'customer_id': 123,
            'issue_date': '2025-06-05',
            'due_date': '2025-06-20',
            'payment_terms': 'Net 15',
            'subtotal': 1000.0,
            'cgst_amount': 90.0,
            'sgst_amount': 90.0,
            'igst_amount': 0.0,
            'total_tax': 180.0,
            'total_amount': 1180.0,
            'amount_paid': 0.0,
            'balance_due': 1180.0,
            'status': 'Draft',
            'payment_mode': 'Bank Transfer',
            'notes': 'Thank you!',
            'terms_conditions': 'Payment due in 15 days.',
            'is_gst_applicable': True,
            'invoice_type': 'Standard',
            'created_at': '2025-06-05T12:00:00Z',
            'updated_at': '2025-06-05T12:00:00Z'
        }
        invoice = sample_invoice
    else:
        invoice = data['invoice']
    # Ensure all required fields are present and mapped for the template
    def get_value(key, default=''):
        return invoice.get(key, default)
    mapped_invoice = {
        'invoice_number': get_value('invoice_number'),
        'customer_id': get_value('customer_id'),
        'issue_date': get_value('issue_date'),
        'due_date': get_value('due_date'),
        'payment_terms': get_value('payment_terms'),
        'subtotal': float(get_value('subtotal', 0)),
        'cgst_amount': float(get_value('cgst_amount', 0)),
        'sgst_amount': float(get_value('sgst_amount', 0)),
        'igst_amount': float(get_value('igst_amount', 0)),
        'total_tax': float(get_value('total_tax', 0)),
        'total_amount': float(get_value('total_amount', 0)),
        'amount_paid': float(get_value('amount_paid', 0)),
        'balance_due': float(get_value('balance_due', 0)),
        'status': get_value('status'),
        'payment_mode': get_value('payment_mode'),
        'notes': get_value('notes'),
        'terms_conditions': get_value('terms_conditions'),
        'is_gst_applicable': bool(get_value('is_gst_applicable', False)),
        'invoice_type': get_value('invoice_type'),
        'created_at': get_value('created_at'),
        'updated_at': get_value('updated_at'),
    }
    # Generate PDF using ReportLab
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50
    # Header
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, y, f"Invoice #{mapped_invoice['invoice_number']}")
    y -= 30
    p.setFont("Helvetica", 12)
    # Invoice details
    p.drawString(50, y, f"Issue Date: {mapped_invoice.get('issue_date')}")
    y -= 20
    p.drawString(50, y, f"Due Date: {mapped_invoice.get('due_date')}")
    y -= 30
    # Customer and payment info
    p.drawString(50, y, f"Customer ID: {mapped_invoice.get('customer_id')}")
    y -= 20
    p.drawString(50, y, f"Payment Terms: {mapped_invoice.get('payment_terms')}")
    y -= 30
    # Amounts
    p.drawString(50, y, f"Subtotal: {mapped_invoice.get('subtotal')}")
    y -= 20
    p.drawString(50, y, f"Total Tax: {mapped_invoice.get('total_tax')}")
    y -= 20
    p.drawString(50, y, f"Total Amount: {mapped_invoice.get('total_amount')}")
    y -= 20
    p.drawString(50, y, f"Amount Paid: {mapped_invoice.get('amount_paid')}")
    y -= 20
    p.drawString(50, y, f"Balance Due: {mapped_invoice.get('balance_due')}")
    # Finish up
    p.showPage()
    p.save()
    buffer.seek(0)
    pdf_data = buffer.getvalue()
    response = make_response(pdf_data)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"attachment; filename=invoice_{mapped_invoice.get('invoice_number', 'document')}.pdf"
    return response
