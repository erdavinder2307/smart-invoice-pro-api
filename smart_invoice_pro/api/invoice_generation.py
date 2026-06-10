import os

from flask import Blueprint, request, make_response, jsonify
from datetime import date
from flasgger import swag_from
import io
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

invoice_generation_blueprint = Blueprint('invoice_generation', __name__)

# ── Upload root — mirrors app.py uploads_root resolution ─────────────────────
_UPLOADS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'uploads')
)

# ── Document-type display labels ──────────────────────────────────────────────
_DOC_LABELS = {
    'invoice':        'INVOICE',
    'quote':          'QUOTATION',
    'purchase_order': 'PURCHASE ORDER',
    'sales_order':    'SALES ORDER',
    'bill':           'BILL',
    'credit_note':    'CREDIT NOTE',
}

# ── Default branding colours (used when no tenant branding is configured) ─────
_DEFAULT_BRANDING = {
    "primary_color":   "#2563EB",
    "secondary_color": "#10B981",
    "accent_color":    "#2d6cdf",
    "logo_url":        "",
    "organization_name": "Solidev Books",
    "invoice_template_settings": {
        "show_logo":      True,
        "show_signature": False,
    },
}


def _resolve_logo_path(logo_url: str) -> str | None:
    """
    Convert a relative logo URL (e.g. /uploads/org_logos/file.png) to
    an absolute filesystem path under _UPLOADS_ROOT.
    Returns None if the URL is empty, unexpected format, or file not found.
    """
    if not logo_url:
        return None
    # Normalise: strip leading slash then strip the leading "uploads/" segment
    rel = logo_url.lstrip('/')
    if rel.startswith('uploads/'):
        rel = rel[len('uploads/'):]
    abs_path = os.path.join(_UPLOADS_ROOT, rel)
    return abs_path if os.path.isfile(abs_path) else None


def _get_tenant_branding(tenant_id: str) -> dict:
    """
    Load branding + org identity from the org-profile document for *tenant_id*.
    Returns _DEFAULT_BRANDING on any failure so the PDF always renders.
    Safe to call from within a Flask request context.
    """
    try:
        from smart_invoice_pro.api.organization_profile_api import _get_profile
        from smart_invoice_pro.api.branding_api import _extract_branding
        profile = _get_profile(tenant_id)
        branding = _extract_branding(profile)
        # Attach org identity fields used by the PDF builder
        branding['organization_name'] = (profile.get('organization_name') or '').strip() or 'Solidev Books'
        branding['org_address'] = profile.get('address') or {}
        branding['gstin'] = (profile.get('gstin') or '').strip()
        return branding
    except Exception:
        return dict(_DEFAULT_BRANDING)


def branding_for_document(document: dict, tenant_id: str) -> dict:
    """
    Return the branding context for a specific document.

    Preference order:
      1. ``document['brand_snapshot']`` — captured at send/issue time (immutable)
      2. Live tenant branding from the org-profile document

    This ensures PDF re-generation for a previously-sent document uses the brand
    that was active at send time, not whatever the tenant has set today.
    """
    snapshot = document.get('brand_snapshot') or {}
    if snapshot:
        live = _get_tenant_branding(tenant_id)
        # Start from live (has invoice_template_settings etc.), overlay snapshot fields
        merged = {**live, **snapshot}
        return merged
    return _get_tenant_branding(tenant_id)


def build_invoice_pdf(
    invoice_data: dict,
    branding: dict | None = None,
    doc_type: str = 'invoice',
    gst_mode: str = 'FULL_GST',
) -> bytes:
    """
    Generate raw PDF bytes for the given invoice_data dict.
    No Flask request context required — safe to call from background jobs.

    Args:
        invoice_data: Invoice/quote/PO/SO data dict.
        branding:     Dict from _extract_branding() + _get_tenant_branding().
                      When None the hardcoded defaults are used.
        doc_type:     One of 'invoice', 'quote', 'purchase_order', 'sales_order'.
                      Controls the title shown in the PDF header.
        gst_mode:     'FULL_GST' | 'COMPOSITION' | 'NO_GST'.
                      Controls GST visibility and composition statutory note.
    """
    if branding is None:
        branding = dict(_DEFAULT_BRANDING)

    accent   = branding.get('accent_color',    _DEFAULT_BRANDING['accent_color'])
    primary  = branding.get('primary_color',   _DEFAULT_BRANDING['primary_color'])
    its      = branding.get('invoice_template_settings') or {}
    show_logo      = bool(its.get('show_logo', True))
    show_signature = bool(its.get('show_signature', False))

    org_name = (branding.get('organization_name') or '').strip() or 'Solidev Books'
    logo_url = branding.get('logo_url', '')
    doc_label = _DOC_LABELS.get(doc_type, doc_type.upper())

    # GST mode flags — control what appears on the PDF
    show_gst = (gst_mode == 'FULL_GST') and bool(invoice_data.get('is_gst_applicable', False))
    show_gstin = gst_mode != 'NO_GST'  # GSTIN shown for Regular and Composition
    is_composition = gst_mode == 'COMPOSITION'
    # Composition statutory note (required by GST Act, Rule 55A)
    _COMPOSITION_NOTE = (
        "Composition Taxable Person. Not eligible to collect tax on supplies."
    )

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
        ('Company',      dict(fontSize=18, textColor=colors.HexColor(accent), spaceAfter=6, leading=22)),
        ('DocTitle',     dict(fontSize=16, alignment=2, spaceAfter=20)),
        ('Footer',       dict(fontSize=9, alignment=1, textColor=colors.grey)),
        ('SignatureLabel', dict(fontSize=9, textColor=colors.grey)),
    ]:
        try:
            styles.add(ParagraphStyle(name=name, **kwargs))
        except KeyError:
            pass

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    # Left cell: logo image (when show_logo=True and logo file exists) or org name text
    logo_path = _resolve_logo_path(logo_url) if show_logo else None
    if logo_path:
        max_logo_w = doc.width * 0.45
        max_logo_h = 2.5 * cm
        left_cell = Image(logo_path, width=max_logo_w, height=max_logo_h,
                          kind='proportional')
    else:
        left_cell = Paragraph(f'<b>{org_name}</b>', styles['Company'])

    header_table = Table([[
        left_cell,
        Paragraph(f'<b>{doc_label}</b>', styles['DocTitle']),
    ]], colWidths=[doc.width * 0.5, doc.width * 0.5])
    header_table.setStyle(TableStyle([
        ('ALIGN',        (1, 0), (1, 0),   'RIGHT'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 12),
    ]))
    story.extend([header_table, Spacer(1, 12)])

    # ── Bill To / Invoice Details ─────────────────────────────────────────────
    bill_to = mapped['customer_name'] or f'Customer ID: {mapped["customer_id"]}'
    gstin_line = f'GSTIN: {branding.get("gstin", "")}' if (show_gstin and branding.get('gstin')) else ''
    gst_status_line = '' if not show_gstin else (
        'GST: Composition Scheme' if is_composition else
        ('GST Applicable: Yes' if show_gst else 'GST: Not Applicable')
    )
    details_right = (
        f'<b>Invoice Details</b><br/>'
        f'Invoice #: {mapped["invoice_number"]}<br/>'
        f'Payment Terms: {mapped["payment_terms"]}<br/>'
        f'Payment Mode: {mapped["payment_mode"]}'
    )
    if gstin_line:
        details_right += f'<br/>{gstin_line}'
    if gst_status_line:
        details_right += f'<br/>{gst_status_line}'

    info_table = Table([[
        Paragraph(
            f'<b>Bill To</b><br/>{bill_to}<br/>'
            f'Status: {mapped["status"]}<br/>'
            f'Issue Date: {mapped["issue_date"]}<br/>'
            f'Due Date: {mapped["due_date"]}',
            styles['Normal'],
        ),
        Paragraph(details_right, styles['Normal']),
    ]], colWidths=[doc.width * 0.48, doc.width * 0.48])
    info_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 12),
    ]))
    story.extend([info_table, Spacer(1, 12)])

    # ── Line items ────────────────────────────────────────────────────────────
    line_items = invoice_data.get('items', [])
    if line_items:
        if show_gst:
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
            # No tax column for Composition/Unregistered
            rows = [['Item', 'Qty', 'Rate', 'Amount']]
            for it in line_items:
                rows.append([
                    it.get('name', ''),
                    f"{float(it.get('quantity', 0)):.2f}",
                    f"\u20b9{float(it.get('rate', 0)):,.2f}",
                    f"\u20b9{float(it.get('amount', 0)):,.2f}",
                ])
            col_widths = [doc.width * w for w in (0.44, 0.12, 0.22, 0.22)]
    else:
        if show_gst:
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
        else:
            rows = [
                ['Description', 'Subtotal', 'Total'],
                [
                    mapped.get('notes', 'Invoice for services rendered'),
                    f"\u20b9{mapped['subtotal']:,.2f}",
                    f"\u20b9{mapped['total_amount']:,.2f}",
                ],
            ]
            col_widths = [doc.width * w for w in (0.5, 0.25, 0.25)]

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

    # ── Terms & conditions ────────────────────────────────────────────────────
    story.append(Paragraph(
        f'<b>Terms &amp; Conditions:</b><br/>'
        f'{mapped.get("terms_conditions", "Payment due as per terms.")}',
        styles['Normal'],
    ))
    story.append(Spacer(1, 16))

    # ── Composition statutory note (GST Act, Rule 55A) ────────────────────────
    if is_composition:
        try:
            comp_style = ParagraphStyle(
                'CompositionNote',
                fontSize=8,
                textColor=colors.HexColor('#7c3aed'),
                leading=12,
                borderPad=4,
            )
            styles.add(comp_style)
        except KeyError:
            comp_style = styles['Normal']
        story.append(Paragraph(
            f'<i>Note: {_COMPOSITION_NOTE}</i>',
            comp_style,
        ))
        story.append(Spacer(1, 8))

    # ── Signature block (show_signature) ──────────────────────────────────────
    if show_signature:
        sig_table = Table([[
            Paragraph(
                f'<b>Authorised Signatory</b><br/><br/><br/>'
                f'____________________________<br/>'
                f'{org_name}',
                styles['SignatureLabel'],
            ),
        ]], colWidths=[doc.width * 0.45])
        sig_table.setStyle(TableStyle([
            ('ALIGN',  (0, 0), (0, 0), 'RIGHT'),
            ('VALIGN', (0, 0), (0, 0), 'BOTTOM'),
        ]))
        story.append(sig_table)
        story.append(Spacer(1, 12))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f'\u00a9 {date.today().year} {org_name}. All rights reserved.',
        styles['Footer'],
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
        pdf_bytes = build_invoice_pdf(invoice, branding=branding, doc_type='invoice')
    except Exception as e:
        return jsonify({'error': f'PDF generation failed: {str(e)}'}), 500

    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = (
        f"attachment; filename=invoice_{invoice.get('invoice_number', 'document')}.pdf"
    )
    return response
