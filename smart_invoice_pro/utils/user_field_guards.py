"""
user_field_guards.py
====================
Prevent tenant-scoped APIs from modifying platform-only user fields.
"""

from __future__ import annotations

PLATFORM_PROTECTED_USER_FIELDS = frozenset({
    "is_super_admin",
})


def reject_protected_user_fields(data: dict) -> str | None:
    """
    Return an error message if ``data`` attempts to set platform-protected fields.
    """
    if not isinstance(data, dict):
        return None
    attempted = sorted(PLATFORM_PROTECTED_USER_FIELDS.intersection(data.keys()))
    if attempted:
        return f"Cannot modify protected fields: {', '.join(attempted)}"
    return None
