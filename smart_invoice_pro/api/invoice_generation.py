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

# ── Default branding colours (used when no tenant branding is configured) ─────
_DEFAULT_BRANDING = {
    "primary_color":   "#2563EB",
    "secondary_color": "#10B981",
    "accent_color":    "#2d6cdf",
    "invoice_template_settings": {
        "show_logo":      True,
        "show_signature": False,
    },
}


def _get_tenant_branding(tenant_id: str) -> dict:
    """
    Load branding from the org-profile document for *tenant_id*.
    Returns _DEFAULT_BRANDING on any failure so the PDF always renders.
    Safe to call from within a Flask request context.
    """
    try:
        from smart_invoice_pro.api.organization_profile_api import _get_profile
        from smart_invoice_pro.api.branding_api import _extract_branding
        profile = _get_profile(tenant_id)
        return _extract_branding(profile)
    except Exception:
        return dict(_DEFAULT_BRANDING)


def build_invoice_pdf(invoice_data: dict, branding: dict | None = None) -> bytes:
    """
    Generate raw PDF bytes for the given invoice_data dict.
    No Flask request context required — safe to call from background jobs or other endpoints.

    *branding* should be the dict returned by _extract_branding() / GET /api/settings/branding.
    When None the hardcoded defaults are used.
    """
    if branding is None:
        branding = dict(_DEFAULT_BRANDING)

    accent   = branding.get('accent_color',    _DEFAULT_BRANDING['accent_color'])
    primary  = branding.get('primary_color',   _DEFAULT_BRANDING['primary_color'])
    its      = branding.get('invoice_template_settings') or {}
    show_logo = bool(its.get('show_logo', True))

    def _get(key, default=''):
        return invoice_data.get(key, default)

    mapped = {
        'invoice_number':    _get('invoice_number'),
        'customer_id':       _get('customer_id'),
        'customer_name':     _get('customer_name'),
        'issue_date':        _get('issue_date'),
        'due_date':          _get('due_date'),
        'payment_terms':     _get('payment_terms'),
        'subtotal':          float(_get('subtotal', 0)),
        'cgst_amount':       float(_get('cgst_amount', 0)),
        'sgst_amount':       float(_get('sgst_amount', 0)),
        'igst_amount':       float(_get('igst_amount', 0)),
        'total_tax':         float(_get('total_tax', 0)),
        'total_amount':      float(_get('total_amount', 0)),
        'amount_paid':       float(_get('amount_paid', 0)),
        'balance_due':       float(_get('balance_due', 0)),
        'status':            _get('status'),
        'payment_mode':      _get('payment_mode'),
        'notes':             _get('notes'),
        'terms_conditions':  _get('terms_conditions'),
        'is_gst_applicable': bool(_get('is_gst_applicable', False)),
        'invoice_type':      _get('invoice_type'),
    }

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=40, rightMargin=40,
                            topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    for name, kwargs in [
        ('Company',      dict(fontSize=20, textColor=colors.HexColor(accent), spaceAfter=10)),
        ('InvoiceTitle', dict(fontSize=16, alignment=2, spaceAfter=20)),
    ]:
        try:
            styles.add(ParagraphStyle(name=name, **kwargs))
        except KeyError:
            pass

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    header_table = Table([[
        Paragraph('<b>Smart Invoice Pro</b>', styles['Company']),
        Paragraph('<b>INVOICE</b>', styles['InvoiceTitle']),
    ]], colWidths=[doc.width * 0.5, doc.width * 0.5])
    header_table.setStyle(TableStyle([
        ('ALIGN',        (1, 0), (1, 0),   'RIGHT'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 12),
    ]))
    story.extend([header_table, Spacer(1, 12)])

    # ── Bill To / Invoice Details ─────────────────────────────────────────────
    bill_to = mapped['customer_name'] or f'Customer ID: {mapped["customer_id"]}'
    info_table = Table([[
        Paragraph(
            f'<b>Bill To</b><br/>{bill_to}<br/>'
            f'Status: {mapped["status"]}<br/>'
            f'Issue Date: {mapped["issue_date"]}<br/>'
            f'Due Date: {mapped["due_date"]}',
            styles['Normal'],
        ),
        Paragraph(
            f'<b>Invoice Details</b><br/>'
            f'Invoice #: {mapped["invoice_number"]}<br/>'
            f'Payment Terms: {mapped["payment_terms"]}<br/>'
            f'Payment Mode: {mapped["payment_mode"]}<br/>'
            f'GST Applicable: {"Yes" if mapped["is_gst_applicable"] else "No"}',
            styles['Normal'],
        ),
    ]], colWidths=[doc.width * 0.48, doc.width * 0.48])
    info_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 12),
    ]))
    story.extend([info_table, Spacer(1, 12)])

    # ── Line items ────────────────────────────────────────────────────────────
    line_items = invoice_data.get('items', [])
    if line_items:
        rows = [['Item', 'Qty', 'Rate', 'Tax %', 'Amount']]
        for it in line_items:
            rows.append([
                it.get('name', ''),
                f"{float(it.get('quantity', 0)):.2f}",
                f"\u20b9{float(it.get('rate', 0)):,.2f}",
                f"{float(it.get('tax', 0)):.1f}%",
                f"\u20b9{float(it.get('amount', 0)):,.2f}",
            ])
        col_widths = [doc.width * w for w in (0.35, 0.12, 0.18, 0.12, 0.18)]
    else:
        rows = [
            ['Description', 'Subtotal', 'CGST', 'SGST', 'IGST', 'Total Tax', 'Total'],
            [
                mapped.get('notes', 'Invoice for services rendered'),
                f"\u20b9{mapped['subtotal']:,.2f}",
                f"\u20b9{mapped['cgst_amount']:,.2f}",
                f"\u20b9{mapped['sgst_amount']:,.2f}",
                f"\u20b9{mapped['igst_amount']:,.2f}",
                f"\u20b9{mapped['total_tax']:,.2f}",
                f"\u20b9{mapped['total_amount']:,.2f}",
            ],
        ]
        col_widths = [doc.width / 7] * 7

    items_table = Table(rows, colWidths=col_widths)
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0),  colors.HexColor(primary).clone(alpha=0.08) if hasattr(colors.HexColor(primary), 'clone') else colors.HexColor('#f0f4fa')),
        ('TEXTCOLOR',  (0, 0), (-1, 0),  colors.HexColor(accent)),
        ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
        ('GRID',       (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTNAME',   (0, 0), (-1, 0),  'Helvetica-Bold'),
    ]))
    story.extend([items_table, Spacer(1, 12)])

    # ── Totals ────────────────────────────────────────────────────────────────
    totals_table = Table([
        ['Amount Paid', f"\u20b9{mapped['amount_paid']:,.2f}"],
        ['Balance Due', f"\u20b9{mapped['balance_due']:,.2f}"],
        ['Grand Total', f"\u20b9{mapped['total_amount']:,.2f}"],
    ], colWidths=[doc.width * 0.6, doc.width * 0.4])
    totals_table.setStyle(TableStyle([
        ('ALIGN',    (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 2), (-1, 2),  'Helvetica-Bold'),
        ('LINEABOVE',(0, 2), (-1, 2),  1.5, colors.HexColor(accent)),
    ]))
    story.extend([totals_table, Spacer(1, 24)])

    # ── Terms & footer ────────────────────────────────────────────────────────
    story.append(Paragraph(
        f'<b>Terms &amp; Conditions:</b><br/>'
        f'{mapped.get("terms_conditions", "Payment due as per terms.")}',
        styles['Normal'],
    ))
    story.append(Spacer(1, 24))
    story.append(Paragraph(
        f'\u00a9 {date.today().year} Smart Invoice Pro. All rights reserved.',
        ParagraphStyle('Footer', fontSize=9, alignment=1, textColor=colors.grey),
    ))
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


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
        return jsonify({'error': 'Missing invoice data'}), 400
    invoice = data['invoice']

    # Fetch tenant branding to style the PDF
    tenant_id = getattr(request, 'tenant_id', None)
    branding = _get_tenant_branding(tenant_id) if tenant_id else None

    try:
        pdf_bytes = build_invoice_pdf(invoice, branding=branding)
    except Exception as e:
        return jsonify({'error': f'PDF generation failed: {str(e)}'}), 500

    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = (
        f"attachment; filename=invoice_{invoice.get('invoice_number', 'document')}.pdf"
    )
    return response
