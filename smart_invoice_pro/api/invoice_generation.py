from flask import Blueprint, request, make_response, jsonify
from datetime import date
from flasgger import swag_from
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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
    # Use Platypus for structured layout
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    # Custom styles: skip if already defined
    try:
        styles.add(ParagraphStyle(name='Company', fontSize=20,
                                  textColor=colors.HexColor('#2d6cdf'), spaceAfter=10))
    except KeyError:
        pass
    try:
        styles.add(ParagraphStyle(name='InvoiceTitle', fontSize=16,
                                  alignment=2, spaceAfter=20))
    except KeyError:
        pass
    story = []
    # Header
    header_data = [[
        Paragraph('<b>Smart Invoice Pro</b>', styles['Company']),
        Paragraph('<b>INVOICE</b>', styles['InvoiceTitle'])
    ]]
    header_table = Table(header_data, colWidths=[doc.width*0.5, doc.width*0.5])
    header_table.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12)
    ]))
    story.append(header_table)
    story.append(Spacer(1, 12))
    # Info sections
    info_data = [[
        Paragraph(f'<b>Bill To</b><br/>'
                  f'Customer ID: {mapped_invoice["customer_id"]}<br/>'
                  f'Status: {mapped_invoice["status"]}<br/>'
                  f'Issue Date: {mapped_invoice["issue_date"]}<br/>'
                  f'Due Date: {mapped_invoice["due_date"]}', styles['Normal']),
        Paragraph(f'<b>Invoice Details</b><br/>'
                  f'Invoice #: {mapped_invoice["invoice_number"]}<br/>'
                  f'Payment Terms: {mapped_invoice["payment_terms"]}<br/>'
                  f'Payment Mode: {mapped_invoice["payment_mode"]}<br/>'
                  f'GST Applicable: {"Yes" if mapped_invoice["is_gst_applicable"] else "No"}',
                  styles['Normal'])
    ]]
    info_table = Table(info_data, colWidths=[doc.width*0.48, doc.width*0.48])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12)
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12))
    # Details table
    details_header = ['Description', 'Subtotal', 'CGST', 'SGST', 'IGST', 'Total Tax', 'Total']
    details_row = [
        mapped_invoice.get('notes', 'Invoice for services rendered'),
        f"{mapped_invoice['subtotal']:,.2f}",
        f"{mapped_invoice['cgst_amount']:,.2f}",
        f"{mapped_invoice['sgst_amount']:,.2f}",
        f"{mapped_invoice['igst_amount']:,.2f}",
        f"{mapped_invoice['total_tax']:,.2f}",
        f"{mapped_invoice['total_amount']:,.2f}"
    ]
    details_data = [details_header, details_row]
    details_table = Table(details_data, colWidths=[doc.width*0.3,]*7)
    details_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f0f4fa')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#2d6cdf')),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
    ]))
    story.append(details_table)
    story.append(Spacer(1, 12))
    # Totals
    totals_data = [
        ['Amount Paid', f"{mapped_invoice['amount_paid']:,.2f}"],
        ['Balance Due', f"{mapped_invoice['balance_due']:,.2f}"],
        ['Grand Total', f"{mapped_invoice['total_amount']:,.2f}"]
    ]
    totals_table = Table(totals_data, colWidths=[doc.width*0.6, doc.width*0.4])
    totals_table.setStyle(TableStyle([
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
        ('FONTNAME', (0,2), (-1,2), 'Helvetica-Bold'),
        ('LINEABOVE', (0,2), (-1,2), 1.5, colors.HexColor('#2d6cdf'))
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 24))
    # Terms & Conditions
    story.append(Paragraph(f'<b>Terms & Conditions:</b><br/>{mapped_invoice.get("terms_conditions", "Payment due as per terms.")}', styles['Normal']))
    story.append(Spacer(1, 24))
    # Footer
    footer_style = ParagraphStyle('Footer', fontSize=9, alignment=1, textColor=colors.grey)
    story.append(Paragraph(f'© {date.today().year} Smart Invoice Pro. All rights reserved.', footer_style))
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    pdf_data = buffer.getvalue()
    response = make_response(pdf_data)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f"attachment; filename=invoice_{mapped_invoice.get('invoice_number', 'document')}.pdf"
    return response
