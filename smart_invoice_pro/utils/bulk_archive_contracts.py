"""Shared response contracts for lifecycle-aware bulk archive operations."""

from copy import deepcopy


FAILURE_CODES = {
    "NOT_FOUND",
    "ALREADY_ARCHIVED",
    "LOCKED_BY_WORKFLOW",
    "DEPENDENCY_BLOCK",
    "FORBIDDEN",
    "VALIDATION_ERROR",
    "INTERNAL_ERROR",
}


def init_bulk_archive_result(entity_type, requested_ids):
    ids = list(requested_ids or [])
    return {
        "entityType": str(entity_type or "").strip().lower(),
        "requestedIds": ids,
        "requestedCount": len(ids),
        "successCount": 0,
        "failedCount": 0,
        "archived": [],
        "failed": [],
        "dependencySummary": {},
        "classification": {
            "referencedRecords": [],
            "independentRecords": [],
            "lockedRecords": [],
            "alreadyArchived": [],
        },
    }


def _merge_dependency_summary(summary, dependency_summary):
    for key, value in (dependency_summary or {}).items():
        summary[key] = int(summary.get(key, 0)) + int(value or 0)


def add_archive_success(result, entity_id, dependency_summary=None, metadata=None):
    item = {"id": entity_id}
    if metadata:
        item.update(deepcopy(metadata))
    result["archived"].append(item)
    result["successCount"] += 1

    dep = dependency_summary or {}
    _merge_dependency_summary(result["dependencySummary"], dep)
    if dep:
        result["classification"]["referencedRecords"].append(entity_id)
    else:
        result["classification"]["independentRecords"].append(entity_id)


def add_archive_failure(result, entity_id, code, reason, dependency_summary=None, metadata=None):
    normalized_code = str(code or "INTERNAL_ERROR").strip().upper()
    if normalized_code not in FAILURE_CODES:
        normalized_code = "INTERNAL_ERROR"

    item = {
        "id": entity_id,
        "code": normalized_code,
        "reason": str(reason or "Archive failed"),
    }
    if metadata:
        item.update(deepcopy(metadata))

    result["failed"].append(item)
    result["failedCount"] += 1

    dep = dependency_summary or {}
    _merge_dependency_summary(result["dependencySummary"], dep)

    if normalized_code == "LOCKED_BY_WORKFLOW":
        result["classification"]["lockedRecords"].append(entity_id)
    elif normalized_code == "ALREADY_ARCHIVED":
        result["classification"]["alreadyArchived"].append(entity_id)
    elif dep:
        result["classification"]["referencedRecords"].append(entity_id)


def finalize_bulk_archive_result(result):
    result["processed"] = [
        {"id": row.get("id"), "action": "archive"}
        for row in result.get("archived", [])
    ]
    result["skipped"] = [
        {"id": row.get("id"), "reason": row.get("reason")}
        for row in result.get("failed", [])
    ]
    result["success_count"] = result.get("successCount", 0)
    result["failure_count"] = result.get("failedCount", 0)
    return result
