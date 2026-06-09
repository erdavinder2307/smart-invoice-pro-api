import base64
import csv
import hashlib
import io
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from smart_invoice_pro.utils.cosmos_client import (
    bank_import_artifacts_container,
    bank_import_batches_container,
    bank_import_jobs_container,
    bank_import_rows_container,
)

try:
    from azure.storage.blob import BlobServiceClient, ContentSettings
except ImportError:  # pragma: no cover - optional for local test runs
    BlobServiceClient = None
    ContentSettings = None


SUPPORTED_PARSE_EXTENSIONS = {"csv", "qif"}
AI_PARSE_EXTENSIONS = {"xlsx", "xls", "pdf"}
REVIEW_ONLY_EXTENSIONS = {"txt", "docx"}
INLINE_ARTIFACT_MAX_BYTES = 700 * 1024
_IMPORT_WORKER = ThreadPoolExecutor(max_workers=int(os.getenv("BANK_IMPORT_WORKERS", "2")))


def utcnow_iso():
    return datetime.utcnow().isoformat() + "Z"


def detect_file_profile(filename, content_type=None):
    extension = ""
    if filename and "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower().strip()

    if extension in SUPPORTED_PARSE_EXTENSIONS:
        return {
            "extension": extension,
            "workflow_mode": "deterministic_parse",
            "supported": True,
            "review_only": False,
        }

    if extension in AI_PARSE_EXTENSIONS:
        return {
            "extension": extension,
            "workflow_mode": "ai_parse",
            "supported": True,
            "review_only": False,
        }

    if extension in REVIEW_ONLY_EXTENSIONS:
        return {
            "extension": extension,
            "workflow_mode": "review_only",
            "supported": True,
            "review_only": True,
        }

    return {
        "extension": extension,
        "workflow_mode": "unsupported",
        "supported": False,
        "review_only": True,
    }


def _normalize_date(raw_value):
    if not raw_value:
        return ""

    text = str(raw_value).strip()
    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%m/%d/%y",
        "%d/%m/%y",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _sanitize_csv_cell(value):
    """Strip CSV formula-injection prefixes from imported cell values."""
    text = str(value or "").strip()
    if text and text[0] in ("=", "+", "-", "@"):
        return f"'{text}"
    return text


def _parse_amount(value):
    if value is None:
        return 0.0
    try:
        return float(re.sub(r"[^\d.\-]", "", str(value)))
    except ValueError:
        return 0.0


def _parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader, start=1):
        row_clean = {
            str(k or "").strip().lower(): _sanitize_csv_cell(v)
            for k, v in row.items()
        }
        date_value = (
            row_clean.get("date")
            or row_clean.get("transaction date")
            or row_clean.get("value date")
            or row_clean.get("posting date")
            or ""
        )
        if "amount" in row_clean:
            amount = _parse_amount(row_clean.get("amount"))
        else:
            debit = _parse_amount(row_clean.get("debit") or 0)
            credit = _parse_amount(row_clean.get("credit") or 0)
            amount = credit - debit

        description = (
            row_clean.get("description")
            or row_clean.get("narration")
            or row_clean.get("particulars")
            or row_clean.get("details")
            or row_clean.get("transaction details")
            or ""
        )

        rows.append(
            {
                "row_index": index,
                "date": _normalize_date(date_value),
                "description": description,
                "amount": round(amount, 2),
                "running_balance": _parse_amount(row_clean.get("balance")),
                "raw_row": row,
            }
        )
    return rows


def _parse_qif(text):
    rows = []
    current = {}
    row_index = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("!"):
            continue
        if line == "^":
            if current:
                row_index += 1
                rows.append(
                    {
                        "row_index": row_index,
                        "date": _normalize_date(current.get("date", "")),
                        "description": current.get("payee") or current.get("memo") or "",
                        "amount": round(_parse_amount(current.get("amount", 0)), 2),
                        "running_balance": None,
                        "raw_row": dict(current),
                    }
                )
            current = {}
            continue

        marker = line[0]
        payload = line[1:]
        if marker == "D":
            current["date"] = payload
        elif marker == "T":
            current["amount"] = payload
        elif marker == "P":
            current["payee"] = payload
        elif marker == "M":
            current["memo"] = payload
    return rows


def _score_candidate(candidate):
    score = 0.35
    warnings = []

    if candidate.get("date"):
        score += 0.25
    else:
        warnings.append("missing_date")

    if candidate.get("description"):
        score += 0.20
    else:
        warnings.append("missing_description")

    if candidate.get("amount") not in (None, 0, 0.0):
        score += 0.20
    else:
        warnings.append("missing_or_zero_amount")

    if candidate.get("running_balance") not in (None, ""):
        score += 0.05

    confidence = round(min(score, 0.99), 2)
    if confidence >= 0.85:
        level = "high"
    elif confidence >= 0.65:
        level = "medium"
    else:
        level = "low"
    return confidence, level, warnings


def _build_row_doc(*, tenant_id, user_id, batch_id, bank_account_id, filename, candidate):
    confidence_score, confidence_level, warnings = _score_candidate(candidate)
    now = utcnow_iso()
    amount = round(float(candidate.get("amount") or 0), 2)
    description = candidate.get("description") or ""
    normalized_date = candidate.get("date") or ""
    fingerprint = hashlib.sha256(
        f"{tenant_id}|{bank_account_id}|{normalized_date}|{amount}|{description.strip().lower()}".encode("utf-8")
    ).hexdigest()

    return {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "batch_id": batch_id,
        "bank_account_id": bank_account_id,
        "source_filename": filename,
        "row_index": int(candidate.get("row_index") or 0),
        "normalized_date": normalized_date,
        "description": description,
        "amount": amount,
        "currency": "INR",
        "direction": "credit" if amount > 0 else "debit" if amount < 0 else "neutral",
        "running_balance": candidate.get("running_balance"),
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "warnings": warnings,
        "review_status": "pending_review" if confidence_level != "high" else "ready",
        "raw_row": candidate.get("raw_row") or {},
        "provenance": {
            "parser": candidate.get("parser"),
            "row_index": candidate.get("row_index"),
            "workflow_stage": "normalized",
        },
        "fingerprint": fingerprint,
        "created_at": now,
        "updated_at": now,
    }


def _get_blob_container_client():
    connection_string = os.getenv("BANK_IMPORT_BLOB_CONNECTION_STRING") or os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BANK_IMPORT_BLOB_CONTAINER", "bank-imports")
    if not connection_string or BlobServiceClient is None:
        return None, container_name

    service = BlobServiceClient.from_connection_string(connection_string)
    container_client = service.get_container_client(container_name)
    try:
        container_client.create_container()
    except Exception:
        pass
    return container_client, container_name


def store_raw_artifact(*, tenant_id, user_id, batch_id, filename, content_type, file_bytes):
    now = utcnow_iso()
    artifact_doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "batch_id": batch_id,
        "artifact_type": "raw_file",
        "filename": filename,
        "content_type": content_type,
        "byte_size": len(file_bytes),
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "created_at": now,
        "updated_at": now,
    }

    container_client, container_name = _get_blob_container_client()
    if container_client is not None:
        month_prefix = datetime.utcnow().strftime("%Y/%m")
        blob_path = f"{tenant_id}/{month_prefix}/{batch_id}/{filename}"
        upload_kwargs = {}
        if ContentSettings is not None and content_type:
            upload_kwargs["content_settings"] = ContentSettings(content_type=content_type)
        container_client.upload_blob(name=blob_path, data=file_bytes, overwrite=True, **upload_kwargs)
        artifact_doc.update(
            {
                "storage_mode": "azure_blob",
                "container_name": container_name,
                "blob_path": blob_path,
                "inline_base64": None,
            }
        )
    else:
        if len(file_bytes) > INLINE_ARTIFACT_MAX_BYTES:
            raise ValueError(
                "File exceeds inline storage limit for local mode. Configure BANK_IMPORT_BLOB_CONNECTION_STRING to reuse Azure Blob storage."
            )
        artifact_doc.update(
            {
                "storage_mode": "inline_cosmos",
                "container_name": None,
                "blob_path": None,
                "inline_base64": base64.b64encode(file_bytes).decode("ascii"),
            }
        )

    bank_import_artifacts_container.create_item(body=artifact_doc)
    return artifact_doc


def _create_job_doc(*, tenant_id, user_id, batch_id):
    now = utcnow_iso()
    job_doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "batch_id": batch_id,
        "status": "queued",
        "stage": "uploaded",
        "progress": 0,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    bank_import_jobs_container.create_item(body=job_doc)
    return job_doc


def _replace_job(job_doc):
    job_doc["updated_at"] = utcnow_iso()
    bank_import_jobs_container.replace_item(item=job_doc["id"], body=job_doc)
    return job_doc


def _replace_batch(batch_doc):
    batch_doc["updated_at"] = utcnow_iso()
    bank_import_batches_container.replace_item(item=batch_doc["id"], body=batch_doc)
    return batch_doc


def _should_process_async():
    explicit = (os.getenv("BANK_IMPORT_ASYNC") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False

    # Keep tests deterministic by default while runtime stays async.
    return not bool(os.getenv("PYTEST_CURRENT_TEST"))


def _run_import_job(*, batch_doc, job_doc, file_profile, file_bytes, pdf_password=""):
    tenant_id = batch_doc["tenant_id"]
    user_id = batch_doc["user_id"]
    bank_account_id = batch_doc.get("bank_account_id")
    filename = batch_doc.get("filename")

    warnings = []
    row_docs = []

    job_doc.update({"status": "running", "stage": "extracting", "progress": 20, "error": None})
    _replace_job(job_doc)

    try:
        parsed_rows = []

        if file_profile["workflow_mode"] == "review_only":
            warnings.append(
                {
                    "code": "REVIEW_ONLY_PHASE1",
                    "message": "This file type is accepted for review-first intake, but deterministic extraction is not enabled in this phase.",
                }
            )
        elif file_profile["workflow_mode"] == "ai_parse":
            from smart_invoice_pro.services.ai_bank_parser_service import parse_xlsx, parse_pdf  # noqa: PLC0415
            if file_profile["extension"] in ("xlsx", "xls"):
                parsed_rows = parse_xlsx(file_bytes, password=pdf_password or "")
            else:  # pdf
                parsed_rows = parse_pdf(file_bytes, password=pdf_password or "")
            for candidate in parsed_rows:
                candidate.setdefault("parser", "ai_claude")
        else:
            text = file_bytes.decode("utf-8", errors="replace")
            parsed_rows = _parse_csv(text) if file_profile["extension"] == "csv" else _parse_qif(text)
            for candidate in parsed_rows:
                candidate["parser"] = file_profile["extension"]

        if parsed_rows:
            job_doc.update({"stage": "normalizing", "progress": 60})
            _replace_job(job_doc)

            for candidate in parsed_rows:
                row_doc = _build_row_doc(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    batch_id=batch_doc["id"],
                    bank_account_id=bank_account_id,
                    filename=filename,
                    candidate=candidate,
                )
                bank_import_rows_container.create_item(body=row_doc)
                row_docs.append(row_doc)

        if not row_docs and file_profile["workflow_mode"] != "review_only":
            warnings.append(
                {
                    "code": "NO_TRANSACTIONS_FOUND",
                    "message": "No transactions were extracted from the uploaded file.",
                }
            )

        warning_count = len(warnings) + sum(len(row.get("warnings") or []) for row in row_docs)
        batch_doc.update(
            {
                "row_count": len(row_docs),
                "warning_count": warning_count,
                "warnings": warnings,
                "status": "review_ready" if row_docs else "review_required",
                "review_status": "review_required" if warnings or any(row.get("review_status") != "ready" for row in row_docs) else "ready",
                "completed_at": utcnow_iso(),
            }
        )
        _replace_batch(batch_doc)

        job_doc.update(
            {
                "status": "completed",
                "stage": batch_doc["status"],
                "progress": 100,
                "completed_at": utcnow_iso(),
                "error": None,
            }
        )
        _replace_job(job_doc)
        return batch_doc, job_doc, row_docs
    except Exception as exc:
        batch_doc.update(
            {
                "status": "failed",
                "review_status": "review_required",
                "warnings": (batch_doc.get("warnings") or []) + [{"code": "PROCESSING_FAILED", "message": str(exc)}],
            }
        )
        _replace_batch(batch_doc)

        job_doc.update(
            {
                "status": "failed",
                "stage": "failed",
                "progress": 100,
                "completed_at": utcnow_iso(),
                "error": str(exc),
            }
        )
        _replace_job(job_doc)
        return batch_doc, job_doc, []


def _enqueue_import_job(*, batch_doc, job_doc, file_profile, file_bytes, pdf_password=""):
    _IMPORT_WORKER.submit(
        _run_import_job,
        batch_doc=batch_doc,
        job_doc=job_doc,
        file_profile=file_profile,
        file_bytes=file_bytes,
        pdf_password=pdf_password,
    )


def create_import_batch(*, tenant_id, user_id, bank_account_id, filename, content_type, file_bytes, pdf_password=""):
    file_profile = detect_file_profile(filename, content_type)
    if not file_profile["supported"]:
        raise ValueError("Unsupported file type for bank import")

    # Synchronous encryption pre-check — raise BEFORE creating any Cosmos documents
    # so the API can return 400 immediately instead of creating a zombie "failed" batch.
    if file_profile["workflow_mode"] == "ai_parse":
        from smart_invoice_pro.services.ai_bank_parser_service import check_file_needs_password  # noqa: PLC0415
        check_file_needs_password(file_bytes, file_profile["extension"], pdf_password)

    now = utcnow_iso()
    batch_doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "bank_account_id": bank_account_id,
        "filename": filename,
        "content_type": content_type,
        "file_size": len(file_bytes),
        "file_extension": file_profile["extension"],
        "workflow_mode": file_profile["workflow_mode"],
        "status": "uploaded",
        "review_status": "review_required",
        "job_id": None,
        "row_count": 0,
        "approved_row_count": 0,
        "warning_count": 0,
        "warnings": [],
        "storage_mode": None,
        "raw_artifact_id": None,
        "approved_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    bank_import_batches_container.create_item(body=batch_doc)

    job_doc = _create_job_doc(tenant_id=tenant_id, user_id=user_id, batch_id=batch_doc["id"])
    batch_doc["job_id"] = job_doc["id"]
    batch_doc["status"] = "processing"
    _replace_batch(batch_doc)

    artifact_doc = store_raw_artifact(
        tenant_id=tenant_id,
        user_id=user_id,
        batch_id=batch_doc["id"],
        filename=filename,
        content_type=content_type,
        file_bytes=file_bytes,
    )
    batch_doc["storage_mode"] = artifact_doc.get("storage_mode")
    batch_doc["raw_artifact_id"] = artifact_doc["id"]

    _replace_batch(batch_doc)

    if _should_process_async():
        _enqueue_import_job(
            batch_doc=dict(batch_doc),
            job_doc=dict(job_doc),
            file_profile=file_profile,
            file_bytes=file_bytes,
            pdf_password=pdf_password,
        )
        return batch_doc, job_doc, []

    return _run_import_job(
        batch_doc=batch_doc,
        job_doc=job_doc,
        file_profile=file_profile,
        file_bytes=file_bytes,
        pdf_password=pdf_password,
    )


def _get_single(container, query):
    items = list(container.query_items(query=query, enable_cross_partition_query=True))
    return items[0] if items else None


def get_batch(*, tenant_id, batch_id):
    return _get_single(
        bank_import_batches_container,
        f"SELECT * FROM c WHERE c.id = '{batch_id}' AND c.tenant_id = '{tenant_id}'",
    )


def get_job(*, tenant_id, job_id):
    return _get_single(
        bank_import_jobs_container,
        f"SELECT * FROM c WHERE c.id = '{job_id}' AND c.tenant_id = '{tenant_id}'",
    )


def list_batches(*, tenant_id, bank_account_id=None):
    query = f"SELECT * FROM c WHERE c.tenant_id = '{tenant_id}'"
    if bank_account_id:
        query += f" AND c.bank_account_id = '{bank_account_id}'"
    query += " ORDER BY c.created_at DESC"
    return list(bank_import_batches_container.query_items(query=query, enable_cross_partition_query=True))


def list_rows(*, tenant_id, batch_id):
    query = (
        f"SELECT * FROM c WHERE c.tenant_id = '{tenant_id}' AND c.batch_id = '{batch_id}' "
        f"ORDER BY c.row_index ASC"
    )
    return list(bank_import_rows_container.query_items(query=query, enable_cross_partition_query=True))


def update_row(*, tenant_id, batch_id, row_id, updates):
    row_doc = _get_single(
        bank_import_rows_container,
        f"SELECT * FROM c WHERE c.id = '{row_id}' AND c.batch_id = '{batch_id}' AND c.tenant_id = '{tenant_id}'",
    )
    if not row_doc:
        return None

    editable_fields = {"normalized_date", "description", "amount", "currency", "review_status", "running_balance"}
    for key, value in (updates or {}).items():
        if key in editable_fields:
            row_doc[key] = value

    if row_doc.get("review_status") == "pending_review":
        row_doc["review_status"] = "reviewed"

    row_doc["updated_at"] = utcnow_iso()
    bank_import_rows_container.replace_item(item=row_id, body=row_doc)
    return row_doc


def delete_batch(*, tenant_id, batch_id):
    """Delete a batch and all its associated rows, job, and artifact documents.

    Returns True if the batch existed and was deleted, False if not found.
    Raises if any Cosmos delete fails.
    """
    batch_doc = get_batch(tenant_id=tenant_id, batch_id=batch_id)
    if not batch_doc:
        return False

    # Prevent deleting a batch that has been approved (transactions already in reconciliation)
    if batch_doc.get("status") == "approved":
        raise ValueError("Cannot delete an approved import batch — its transactions are already in reconciliation.")

    # Delete all rows for this batch
    rows = list(bank_import_rows_container.query_items(
        query=f"SELECT c.id FROM c WHERE c.batch_id = '{batch_id}' AND c.tenant_id = '{tenant_id}'",
        enable_cross_partition_query=True,
    ))
    for row in rows:
        try:
            bank_import_rows_container.delete_item(item=row["id"], partition_key=tenant_id)
        except Exception:
            pass  # best-effort cleanup

    # Delete the associated job if present
    job_id = batch_doc.get("job_id")
    if job_id:
        try:
            bank_import_jobs_container.delete_item(item=job_id, partition_key=tenant_id)
        except Exception:
            pass

    # Delete the raw artifact if present
    artifact_id = batch_doc.get("raw_artifact_id")
    if artifact_id:
        try:
            bank_import_artifacts_container.delete_item(item=artifact_id, partition_key=tenant_id)
        except Exception:
            pass

    # Delete the batch itself
    bank_import_batches_container.delete_item(item=batch_id, partition_key=tenant_id)
    return True


def mark_batch_approved(*, tenant_id, batch_id, approved_row_count):
    batch_doc = get_batch(tenant_id=tenant_id, batch_id=batch_id)
    if not batch_doc:
        return None

    batch_doc["status"] = "reconciliation_prepared"
    batch_doc["review_status"] = "approved"
    batch_doc["approved_row_count"] = approved_row_count
    batch_doc["approved_at"] = utcnow_iso()
    batch_doc["updated_at"] = utcnow_iso()
    bank_import_batches_container.replace_item(item=batch_id, body=batch_doc)
    return batch_doc