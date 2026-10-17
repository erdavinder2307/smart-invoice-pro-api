#!/usr/bin/env python3
"""
set_trial_end_dates.py
======================
Gives every open-ended trial organisation an end date. Organisations created
before trials had an end (no trial_ends_at) keep full access until this runs.

DRY RUN by default: prints, per organisation, its id, name, created_at and the
proposed trial_ends_at, and writes nothing. --apply writes only trial_ends_at
and updated_at. Skipped: DEMO and INTERNAL organisations, organisations not on
the trial plan, and organisations that already have trial_ends_at.

Usage
-----
  python scripts/set_trial_end_dates.py --end-date YYYY-MM-DD [--apply]

--end-date is the day the trial ends (00:00 UTC that day). Pick it at least a
few days ahead so people can be told first.

Environment variables required (same as main app):
  COSMOS_URI, COSMOS_KEY, COSMOS_DB_NAME

The output names organisations: keep it outside the repo and OneDrive.
"""
import argparse
import os
import sys
from datetime import datetime

# ── Allow running from the repo root ────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

EXEMPT_TYPES = {'DEMO', 'INTERNAL'}


def needs_end_date(tenant):
    """True for a trial organisation (not demo/internal) without trial_ends_at."""
    if (tenant.get('tenant_type') or '').upper() in EXEMPT_TYPES:
        return False
    if (tenant.get('plan') or 'trial').strip().lower() != 'trial':
        return False
    return not tenant.get('trial_ends_at')


def parse_end_date(value):
    """'YYYY-MM-DD' -> ISO timestamp at 00:00 UTC (the format tenant documents use)."""
    return datetime.strptime(value, '%Y-%m-%d').isoformat()


def run(container, end_iso, apply, now=None, out=print):
    now_iso = (now or datetime.utcnow()).isoformat()
    tenants = list(container.query_items(
        query='SELECT * FROM c',
        enable_cross_partition_query=True,
    ))
    targets = [t for t in tenants if needs_end_date(t)]
    out(f"{'APPLY' if apply else 'DRY RUN'}: {len(targets)} of {len(tenants)} organisations get trial_ends_at {end_iso}")
    for t in targets:
        out(f"  {t.get('id')}  {t.get('name')!r}  created {t.get('created_at')}  -> {end_iso}")
        if apply:
            t['trial_ends_at'] = end_iso
            t['updated_at'] = now_iso
            container.replace_item(item=t['id'], body=t)
    if not apply:
        out('Nothing written. Add --apply to write.')
    return targets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--end-date', required=True, help='YYYY-MM-DD, trial ends 00:00 UTC that day')
    parser.add_argument('--apply', action='store_true', help='write the dates (default: dry run)')
    args = parser.parse_args(argv)

    try:
        end_iso = parse_end_date(args.end_date)
    except ValueError:
        parser.error('--end-date must look like 2026-11-15')
    if datetime.fromisoformat(end_iso) <= datetime.utcnow():
        parser.error('--end-date must be in the future')

    from smart_invoice_pro.utils.cosmos_client import tenants_container
    run(tenants_container, end_iso, args.apply)


if __name__ == '__main__':
    main()
