# Redis in Solidev Books

**Strategy:** Cost-first. Redis is optional — if unavailable the app falls back gracefully to Cosmos DB reads and in-memory rate limiting.

---

## What Redis adds

| Feature | Without Redis | With Redis |
|---|---|---|
| Permission checks | 2 Cosmos reads per API call | Cached 5 min — 0 reads on hit |
| Dashboard summary | 5–6 Cosmos reads per load (cache was broken) | Cached 5 min — 0 reads on hit |
| Rate limiting | In-memory (lost on restart, not shared) | Redis (persistent, shared across instances) |
| Background jobs | In-process APScheduler only | Queue-ready for future async jobs |

---

## Local development

### Prerequisites

- Docker and Docker Compose

### Start Redis

```bash
cd smart-invoice-pro-api-2
docker compose -f docker-compose.redis.yml up -d
```

### Verify it is running

```bash
docker compose -f docker-compose.redis.yml ps
# Expected: solidev_redis_dev  running (healthy)

docker compose -f docker-compose.redis.yml exec redis redis-cli ping
# Expected: PONG
```

### Set the local environment variable

Add to your `.env` file in `smart-invoice-pro-api-2/`:

```env
REDIS_URL=redis://localhost:6379/0
```

### Stop Redis

```bash
docker compose -f docker-compose.redis.yml down
```

### Inspect cache in the CLI

```bash
docker compose -f docker-compose.redis.yml exec redis redis-cli

# List all keys
KEYS *

# Inspect a permission cache entry
GET permissions:my-tenant-id:my-user-id

# Inspect a dashboard cache entry
KEYS dashboard:*

# Flush all keys (dev only)
FLUSHDB
```

---

## Azure production setup

See the commands below to provision **Azure Cache for Redis Basic C0** — the lowest-cost tier, sufficient for validation and early growth stage.

### Estimated monthly cost (Basic C0, Central India, as of 2026)

| SKU | RAM | Cost/month (approx.) |
|---|---|---|
| Basic C0 | 250 MB | ~$16 USD |
| Basic C1 | 1 GB | ~$55 USD |

C0 is sufficient for permission caching, dashboard caching, and rate limiting up to ~1,000 daily active users.

### Provision

```bash
# Variables — change these to match your resource group and location
RG=solidev
LOCATION=centralindia
REDIS_NAME=solidev-redis-dev
APP_SERVICE_NAME=smartinvoicepro

# 1. Create Redis instance (Basic C0)
az redis create \
  --resource-group $RG \
  --name $REDIS_NAME \
  --location $LOCATION \
  --sku Basic \
  --vm-size c0

# 2. Wait for provisioning (takes ~10-15 minutes)
az redis show \
  --resource-group $RG \
  --name $REDIS_NAME \
  --query "provisioningState"

# 3. Get primary access key
az redis list-keys \
  --resource-group $RG \
  --name $REDIS_NAME \
  --query "primaryKey" -o tsv

# 4. Get host name
az redis show \
  --resource-group $RG \
  --name $REDIS_NAME \
  --query "hostName" -o tsv
# Returns: solidev-redis-dev.redis.cache.windows.net
```

### Set the connection string on App Service

```bash
# Replace YOUR_PRIMARY_KEY with the key from step 3
az webapp config appsettings set \
  --resource-group $RG \
  --name $APP_SERVICE_NAME \
  --settings \
    REDIS_URL="rediss://solidev-redis-dev.redis.cache.windows.net:6380?password=YOUR_PRIMARY_KEY&ssl=True"
```

> Note: Azure Cache for Redis uses port **6380** with SSL (`rediss://`), not port 6379.

### Full environment variable reference

| Variable | Local value | Azure value |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | `rediss://<hostname>:6380?password=<key>&ssl=True` |
| `ENABLE_REDIS_CACHE` | `true` (optional override) | `true` |

---

## Cache key reference

| Key pattern | TTL | Populated by | Invalidated by |
|---|---|---|---|
| `permissions:{tenant_id}:{user_id}` | 300s | `rbac_resolver.resolve_user_permissions` | Role change, permission change |
| `dashboard:{tenant_id}:{range}:{start}:{end}` | 300s | `dashboard_api.dashboard_summary` | TTL expiry (validation stage) |
| `rate_limit:*` | Managed by Flask-Limiter | Flask-Limiter | Auto-expired by Flask-Limiter |

---

## Graceful degradation

If `REDIS_URL` is not set, or if Redis is unreachable, the application continues to work using:

- Cosmos DB reads for every permission check (original behavior)
- The fixed in-memory dashboard cache (5-minute TTL, per-process)
- In-memory Flask-Limiter (not shared across instances)

No restarts required. Redis availability is checked at startup and again on any connection error.

---

## Background job queue (foundation)

The `smart_invoice_pro/services/job_queue.py` module provides a Redis list-backed job queue for future async processing. Jobs not yet migrated:

| Job | Current mechanism | Future |
|---|---|---|
| Payment reminders | APScheduler (in-process) | Queue worker |
| Low-stock email alerts | Azure Function timer | Queue worker |
| Invoice PDF generation for email | Synchronous in handler | Queue worker |
| Demo reset | Not implemented | Queue worker |
| Report generation | Synchronous in handler | Queue worker |

To push a job manually (testing):

```python
from smart_invoice_pro.services.job_queue import push_job

push_job("emails", {
    "type": "payment_reminder",
    "invoice_id": "abc-123",
    "tenant_id": "tenant-xyz",
})
```

---

## Security notes

- Never commit `.env` files with real Redis passwords.
- In production, use Azure's access-key authentication over TLS (`rediss://`).
- Redis stores permission data — treat it as sensitive. Restrict VNet access to App Service only.
- The Basic C0 tier does not support VNet injection. Upgrade to Standard C1+ for network isolation in production.
