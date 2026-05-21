# Solidev Books Lifecycle Architecture Upgrade Report

Date: 2026-05-13

## 1) Dependency Architecture Report

### Implemented lifecycle core
- Central policy and execution engine: `smart_invoice_pro/utils/lifecycle_service.py`
- Centralized dependency resolver expanded: `smart_invoice_pro/utils/dependency_checker.py`
- Generic lifecycle APIs:
  - `GET /api/lifecycle/<entity_type>/<entity_id>/analysis`
  - `POST /api/lifecycle/<entity_type>/<entity_id>/execute`
  - `POST /api/lifecycle/<entity_type>/bulk-execute`
- Blueprint registration: `smart_invoice_pro/app.py`

### Dependency model used by lifecycle analysis
- Product dependencies:
  - invoices.items.product_id
  - quotes.items.product_id
  - sales_orders.items.product_id
  - purchase_orders.items.product_id
- Customer dependencies:
  - invoices.customer_id
  - quotes.customer_id
  - sales_orders.customer_id
- Vendor dependencies:
  - purchase_orders.vendor_id
  - bills.vendor_id
- Quote dependencies:
  - invoices.converted_from_quote_id
  - sales_orders.converted_from_quote_id
- Sales order dependencies:
  - invoices.converted_from_so_id
- Recurring profile dependencies:
  - invoices.recurring_profile_id
- Bank account dependencies (conservative):
  - expenses.bank_account_id
  - bills.bank_account_id
- Role dependencies (conservative):
  - users.role_id

## 2) Lifecycle Strategy Matrix (Policy)

### Master rule
- If no dependencies and not accounting-protected: hard delete
- If dependencies exist: archive
- If accounting-protected: archive regardless of dependencies

### Entity policy
- Product/Item: delete if unlinked; archive if linked
- Customer: delete if unlinked; archive if linked
- Vendor: delete if unlinked; archive if linked
- Quote: delete if unconverted/unlinked; archive if linked
- Sales Order: archive (accounting/workflow protected)
- Purchase Order: archive (accounting/workflow protected)
- Bill: archive (financial transaction)
- Expense: archive (financial transaction)
- Invoice: archive (financial transaction)
- Payment: archive (financial transaction)
- Reconciliation: archive (financial transaction)
- Recurring Profile: delete if no generated invoice dependency; archive if linked
- Tax/Tax Rate: archive/deactivate only
- Audit/Event/Workflow logs: archive/retain only
- Notifications: archive/deactivate preferred
- Users/Roles: deactivate/archive preferred; avoid physical delete when linked
- Bank Accounts: delete only if unlinked and no txn usage; otherwise archive/deactivate

## 3) Unsafe Delete Risks Found

- Hard delete of accounting records would break audit trail and statutory retention.
- Mixed per-module delete behavior risked inconsistent UI expectations.
- Existing endpoint patterns had archive-only assumptions but no centralized policy engine.
- Cosmos partition-key-specific deletes can fail if key resolution is inconsistent.
- Duplicate legacy delete/archive blocks in some API files increase drift risk.

## 4) Recommended Delete/Archive Behavior

### Enforced in new engine
- `requestedAction=delete` now resolves to `performedAction=delete|archive` via policy + dependencies.
- Accounting-protected entities are forced to archive.
- Response includes:
  - `performedAction`
  - `hardDeleteAllowed`
  - `dependencySummary`

### Bulk behavior
- New mixed-mode bulk endpoint returns per-record result and aggregate counts:
  - `deletedCount`
  - `archivedCount`
  - `restoredCount`
  - `failedCount`

## 5) Files/Modules Updated

### Backend
- Added:
  - `smart_invoice_pro/utils/lifecycle_service.py`
  - `smart_invoice_pro/api/lifecycle_api.py`
- Updated:
  - `smart_invoice_pro/utils/dependency_checker.py`
  - `smart_invoice_pro/app.py`
  - `smart_invoice_pro/api/product_api.py`
  - `smart_invoice_pro/api/customers_api.py`
  - `smart_invoice_pro/api/vendors_api.py`
  - `smart_invoice_pro/api/quotes_api.py`
  - `smart_invoice_pro/api/invoices.py`
  - `smart_invoice_pro/api/sales_orders_api.py`
  - `smart_invoice_pro/api/bills_api.py`
  - `smart_invoice_pro/api/expenses_api.py`
  - `smart_invoice_pro/api/purchase_orders_api.py`
  - `smart_invoice_pro/api/recurring_profiles_api.py`

### Frontend
- Updated shared lifecycle services:
  - `src/services/archiveService.js`
  - `src/services/bulkArchiveService.js`
- Updated unified dialog behavior/copy:
  - `src/components/common/LifecycleArchiveDialog.jsx`
- Test alignment:
  - `src/__tests__/components/ProductListInventoryConsole.test.jsx`

## 6) Backend Architecture Improvements

- Introduced centralized decision point for all lifecycle actions.
- Added generic lifecycle API to avoid duplicated delete-policy logic in every module.
- Preserved audit/domain-event hooks for archive/delete actions.
- Introduced alias normalization for entity types to keep endpoint contracts stable.

## 7) UX/Dialog Improvements

- Single-record dialog now dynamically shows:
  - Safe delete message when hard delete is allowed
  - Archive explanation when dependencies/policy block delete
  - Dependency counts summary
- Bulk dialog now explains mixed outcomes (delete vs archive).
- Confirmation labels now reflect action semantics (`Delete`, `Archive`, `Process All`).

## 8) Cosmos DB Optimization Recommendations

### Immediate
- Move unlinked hard-deleted operational master data through lifecycle engine.
- Keep financial/audit entities archived only.
- Add filtered active-only queries consistently (`status != ARCHIVED`, `is_deleted = false`).

### Medium-term
- Add scheduled archival compaction jobs by partition:
  - tenant-wise rolling retention for non-financial entities
  - move aged archived operational data to cold storage snapshot (Blob/Data Lake)
- Review indexing policy:
  - reduce indexing on rarely queried archival metadata fields
  - composite indexes for active workflows only
- Add RU telemetry for lifecycle endpoints:
  - dependency-check query costs
  - bulk execution costs by entity type

### Long-term
- Introduce archive shadow containers for heavy entities if active+archived coexistence increases RU (quotes, products, customers).
- Partition-aware batched cleanup tooling with dry-run mode.

## 9) Final Implementation Summary

- Implemented enterprise lifecycle foundation with centralized policy, dependency analysis, and mixed bulk handling.
- Updated shared frontend lifecycle path so all list screens using unified dialog now run intelligent lifecycle actions.
- Converted major API delete handlers to centralized smart decision engine, preserving accounting protection constraints.
- Added new generic lifecycle API to support future module migration without duplicating logic.

## 10) Open Migration Items (Remaining for full parity)

- Migrate modules that still call legacy direct delete APIs outside `LifecycleArchiveDialog` flows (e.g., selected settings/admin delete paths) to `POST /api/lifecycle/.../execute`.
- Standardize old per-module bulk handlers to delegate to lifecycle bulk engine or deprecate them in favor of `/api/lifecycle/<entity>/bulk-execute`.
- Add backend unit tests for:
  - hard-delete path (no dependencies)
  - archive-on-dependency path
  - accounting-protected entities force-archive
  - mixed bulk execution aggregation
- Add contract tests for frontend service expectations on `performedAction` and count summaries.
