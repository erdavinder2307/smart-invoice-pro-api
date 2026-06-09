"""Shared validators for document line items."""


def validate_line_item_rates(items):
    """Return field-level errors for zero/negative rates on meaningful lines."""
    errors = {}
    if not isinstance(items, list):
        return errors
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        try:
            qty = float(item.get('quantity') or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        try:
            rate = float(item.get('rate') or 0)
        except (TypeError, ValueError):
            rate = 0
        if rate <= 0:
            errors[f'items[{idx}].rate'] = 'Rate must be greater than zero'
    return errors
