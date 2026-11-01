# smart-invoice-pro-api-2 (backend)

Flask app factory pattern — the real entrypoint is `smart_invoice_pro/app.py`
(`create_app()`), run via `main.py`. `requirements.txt` also lists FastAPI and
SQLAlchemy — those are present for specific subsystems/migrations, not an
alternate entrypoint; don't assume this is a FastAPI app. Deployed as Azure
Functions (`azure-functions/`) backed by Azure Cosmos DB.

## Commands
- `./run_backend.sh` — activates `venv/` and runs `python3 main.py` on port 5001
- `./test_all_apis.sh` — API smoke tests
- `pytest` — unit tests (uses local `venv`, not the workspace-root one)

## Docs
`AZURE_DEPLOYMENT.md`, `AZURE_MIGRATION_SUMMARY.md`, `LIFECYCLE_ARCHITECTURE_UPGRADE_REPORT.md`,
`README_REDIS.md`, `INVENTORY_FEATURES.md` live at this level.

## Do not read/quote
`venv/`, `__pycache__/` (present in multiple subpackages) — generated.
