COSMOS_INTERNAL_FIELDS = {
    "_rid",
    "_self",
    "_etag",
    "_attachments",
    "_ts",
}

DEFAULT_SENSITIVE_FIELDS = {
    "password",
    "portal_password",
}


def sanitize_item(item, additional_sensitive_fields=None):
    if not isinstance(item, dict):
        return item

    sensitive = set(DEFAULT_SENSITIVE_FIELDS)
    if additional_sensitive_fields:
        sensitive.update(additional_sensitive_fields)

    cleaned = {}
    for key, value in item.items():
        if key in COSMOS_INTERNAL_FIELDS:
            continue
        if key in sensitive:
            continue
        cleaned[key] = value

    return cleaned


def sanitize_items(items, additional_sensitive_fields=None):
    return [sanitize_item(i, additional_sensitive_fields) for i in items]
