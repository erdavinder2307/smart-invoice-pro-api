"""CSV export helpers for compliance-grade audit trails."""

import csv
import io
import json


EXPORT_COLUMNS = [
    "created_at",
    "action",
    "category",
    "risk_level",
    "summary",
    "entity",
    "entity_label",
    "entity_id",
    "user_name",
    "user_email",
    "user_id",
    "ip_address",
    "user_agent",
]

ADMIN_EXPORT_COLUMNS = ["tenant_id", "tenant_name", *EXPORT_COLUMNS]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def audit_rows_to_csv(rows, *, include_tenant=False):
    """Serialize enriched audit rows to CSV text."""
    columns = ADMIN_EXPORT_COLUMNS if include_tenant else EXPORT_COLUMNS
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows or []:
        writer.writerow([_cell(row.get(col)) for col in columns])
    return output.getvalue()
