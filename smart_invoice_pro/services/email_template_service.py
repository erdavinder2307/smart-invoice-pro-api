"""
Shared branded email template service.

Usage:
    from smart_invoice_pro.services.email_template_service import render_branded_email

    html, plain = render_branded_email(
        doc_type='invoice',
        context={
            'doc_number':   'INV-0042',
            'customer_name': 'Acme Corp',
            'issue_date':   '2026-06-09',
            'due_date':     '2026-06-23',
            'total_amount': 15000.00,
            'balance_due':  15000.00,
            'subtotal':     12711.86,
            'total_tax':    2288.14,
            'items':        [...],              # list of line-item dicts
            'message':      '',                # optional personal message
            'portal_url':   'https://...',     # optional View Online link
            'is_gst_applicable': True,
        },
        branding={
            'primary_color':    '#2563EB',
            'accent_color':     '#2d6cdf',
            'organization_name': 'Acme Consulting',
        },
    )
"""

from __future__ import annotations

_DOC_LABELS = {
    'invoice':        'Invoice',
    'quote':          'Quotation',
    'purchase_order': 'Purchase Order',
    'sales_order':    'Sales Order',
    'reminder':       'Payment Reminder',
    'bill':           'Bill',
}

_DEFAULT_PRIMARY = '#2563EB'
_DEFAULT_ORG     = 'Solidev Books'


def _fmt(amount: float) -> str:
    return f"\u20b9{amount:,.2f}"


def _item_rows_html(items: list) -> str:
    rows = ''
    for item in items:
        name   = item.get('name') or item.get('product_name') or ''
        qty    = float(item.get('quantity', 1))
        rate   = float(item.get('rate') or item.get('unit_price', 0))
        amount = float(item.get('amount') or item.get('total', 0))
        rows += (
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{name}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{qty:.2f}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{_fmt(rate)}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0;text-align:right'>{_fmt(amount)}</td>"
            f"</tr>"
        )
    return rows


def render_branded_email(
    doc_type: str,
    context: dict,
    branding: dict,
) -> tuple[str, str]:
    """
    Build branded HTML + plain-text email bodies.

    Returns:
        (html_body: str, plain_body: str)
    """
    primary  = (branding.get('primary_color') or _DEFAULT_PRIMARY).strip()
    org_name = (branding.get('organization_name') or '').strip() or _DEFAULT_ORG
    label    = _DOC_LABELS.get(doc_type, doc_type.replace('_', ' ').title())

    doc_number    = context.get('doc_number', '')
    customer_name = context.get('customer_name', 'Customer')
    issue_date    = context.get('issue_date', '')
    due_date      = context.get('due_date', '')
    total_amount  = float(context.get('total_amount', 0))
    balance_due   = float(context.get('balance_due', total_amount))
    subtotal      = float(context.get('subtotal', 0))
    total_tax     = float(context.get('total_tax', 0))
    items         = context.get('items', [])
    message       = (context.get('message') or '').strip()
    portal_url    = (context.get('portal_url') or '').strip()

    personal_msg_html = (
        f"<p style='color:#475569'>{message}</p>" if message else ''
    )

    items_html = ''
    if items:
        rows = _item_rows_html(items)
        items_html = f"""
        <table style='width:100%;border-collapse:collapse;margin:16px 0'>
            <thead>
                <tr style='background:#F8FAFC'>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>Item</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Qty</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Rate</th>
                    <th style='padding:8px;border:1px solid #e0e0e0;text-align:right'>Amount</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """

    totals_html = f"""
    <table style='width:100%;border-collapse:collapse;margin-top:8px'>
        <tr>
            <td style='padding:4px 8px;color:#475569'>Subtotal</td>
            <td style='padding:4px 8px;text-align:right'>{_fmt(subtotal)}</td>
        </tr>
        <tr>
            <td style='padding:4px 8px;color:#475569'>Tax</td>
            <td style='padding:4px 8px;text-align:right'>{_fmt(total_tax)}</td>
        </tr>
        <tr style='font-weight:bold;font-size:16px'>
            <td style='padding:8px;border-top:2px solid #E2E8F0'>Total</td>
            <td style='padding:8px;border-top:2px solid #E2E8F0;text-align:right'>{_fmt(total_amount)}</td>
        </tr>
        <tr style='color:#D97706'>
            <td style='padding:4px 8px'>Balance Due</td>
            <td style='padding:4px 8px;text-align:right;font-weight:bold'>{_fmt(balance_due)}</td>
        </tr>
    </table>
    """

    view_link_html = ''
    if portal_url:
        view_link_html = (
            f"<p style='margin-top:20px'>"
            f"<a href='{portal_url}' "
            f"style='background:{primary};color:#fff;padding:10px 20px;"
            f"border-radius:4px;text-decoration:none'>View {label} Online</a></p>"
        )

    meta_row = ''
    if issue_date or due_date:
        meta_row = (
            f"<p style='margin-top:16px;color:#475569'>"
            + (f"<strong>Issue Date:</strong> {issue_date} &nbsp;|&nbsp;" if issue_date else '')
            + (f"<strong>Due Date:</strong> {due_date}" if due_date else '')
            + "</p>"
        )

    html = f"""
<html>
<body style='font-family:Inter,Arial,sans-serif;color:#0F172A;max-width:640px;margin:auto'>
    <div style='background:{primary};padding:24px;border-radius:8px 8px 0 0'>
        <h2 style='color:#fff;margin:0'>{label} {doc_number}</h2>
    </div>
    <div style='background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px'>
        <p>Dear {customer_name},</p>
        {personal_msg_html}
        <p>Please find your {label.lower()} details below:</p>
        {items_html}
        {totals_html}
        {meta_row}
        {view_link_html}
        <p style='color:#94A3B8;font-size:12px;margin-top:32px'>
            This is an automated email from {org_name}.
        </p>
    </div>
</body>
</html>
"""

    plain = (
        f"Dear {customer_name},\n\n"
        f"{message + chr(10) + chr(10) if message else ''}"
        f"{label}: {doc_number}\n"
        f"Total: {_fmt(total_amount)}\n"
        f"Balance Due: {_fmt(balance_due)}\n"
        + (f"Issue Date: {issue_date}\n" if issue_date else '')
        + (f"Due Date: {due_date}\n" if due_date else '')
        + (f"\nView online: {portal_url}\n" if portal_url else '')
        + f"\nThank you,\n{org_name}"
    )

    return html, plain


def render_reminder_email(
    context: dict,
    branding: dict,
) -> tuple[str, str]:
    """
    Convenience wrapper for payment reminders.
    context keys: doc_number, customer_name, balance_due, due_date
    """
    primary  = (branding.get('primary_color') or _DEFAULT_PRIMARY).strip()
    org_name = (branding.get('organization_name') or '').strip() or _DEFAULT_ORG

    doc_number    = context.get('doc_number', '')
    customer_name = context.get('customer_name', 'Customer')
    balance_due   = float(context.get('balance_due', 0))
    due_date      = context.get('due_date', 'N/A')

    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:{primary};padding:24px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0">Payment Reminder</h2>
  </div>
  <div style="background:#fff;padding:24px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 8px 8px">
    <p>Dear {customer_name},</p>
    <p>This is a friendly reminder that Invoice <strong>{doc_number}</strong> has an outstanding balance.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px;background:#F8FAFC"><strong>Invoice #</strong></td>
          <td style="padding:8px">{doc_number}</td></tr>
      <tr><td style="padding:8px;background:#F8FAFC"><strong>Balance Due</strong></td>
          <td style="padding:8px;color:#DC2626"><strong>{_fmt(balance_due)}</strong></td></tr>
      <tr><td style="padding:8px;background:#F8FAFC"><strong>Due Date</strong></td>
          <td style="padding:8px">{due_date}</td></tr>
    </table>
    <p>Please arrange payment at your earliest convenience.</p>
    <p>Thank you,<br/><strong>{org_name}</strong></p>
  </div>
</div>
"""

    plain = (
        f"Dear {customer_name},\n\n"
        f"This is a friendly payment reminder for Invoice {doc_number}.\n"
        f"Balance Due: {_fmt(balance_due)}\n"
        f"Due Date: {due_date}\n\n"
        f"Please arrange payment at your earliest convenience.\n\n"
        f"Thank you,\n{org_name}"
    )

    return html, plain
