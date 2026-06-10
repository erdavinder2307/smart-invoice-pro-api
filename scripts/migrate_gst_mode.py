#!/usr/bin/env python3
"""
migrate_gst_mode.py
===================
One-time migration: derive and persist `gst_mode` on every organization_profile
document that does not yet have it.

gst_mode values
---------------
  FULL_GST    – Regular taxpayer
  COMPOSITION – Composition scheme
  NO_GST      – Unregistered / GST disabled

Safe to run multiple times (idempotent).

Usage
-----
  cd smart-invoice-pro-api-2
  python scripts/migrate_gst_mode.py [--dry-run]

Environment variables required (same as main app):
  COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE

"""
import argparse
import sys
import os

# ── Allow running from the repo root ────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def derive_gst_mode(profile: dict) -> str:
    """Pure derivation — no I/O."""
    reg_type = (profile.get('gst_registration_type') or '').strip().lower()
    gst_enabled = profile.get('gst_enabled')

    if reg_type == 'unregistered':
        return 'NO_GST'
    if reg_type == 'composition':
        return 'COMPOSITION'
    if reg_type == 'regular':
        return 'FULL_GST'

    # Legacy fallback: gst_enabled boolean
    if gst_enabled is False:
        return 'NO_GST'
    return 'FULL_GST'


def run(dry_run: bool = False) -> None:
    from smart_invoice_pro.utils.cosmos_client import settings_container

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Starting gst_mode migration…\n")

    all_profiles = list(settings_container.query_items(
        query="SELECT * FROM c WHERE c.type = 'organization_profile'",
        enable_cross_partition_query=True,
    ))

    total     = len(all_profiles)
    updated   = 0
    skipped   = 0
    errors    = 0

    for profile in all_profiles:
        tenant_id = profile.get('tenant_id', '?')
        try:
            existing_mode = (profile.get('gst_mode') or '').strip().upper()
            derived_mode  = derive_gst_mode(profile)

            if existing_mode in ('FULL_GST', 'COMPOSITION', 'NO_GST'):
                print(f"  SKIP  tenant={tenant_id!r}  gst_mode already={existing_mode!r}")
                skipped += 1
                continue

            print(
                f"  {'WOULD SET' if dry_run else 'SET'}  "
                f"tenant={tenant_id!r}  "
                f"reg_type={profile.get('gst_registration_type', '—')!r}  "
                f"gst_enabled={profile.get('gst_enabled', '—')}  "
                f"→ gst_mode={derived_mode!r}"
            )

            if not dry_run:
                profile['gst_mode']    = derived_mode
                # Keep gst_enabled in sync for backward compat
                profile['gst_enabled'] = (derived_mode != 'NO_GST')
                settings_container.upsert_item(profile)

            updated += 1

        except Exception as exc:
            print(f"  ERROR  tenant={tenant_id!r}  {exc}", file=sys.stderr)
            errors += 1

    print(
        f"\n{'[DRY RUN] ' if dry_run else ''}Migration complete.\n"
        f"  Total profiles : {total}\n"
        f"  Updated        : {updated}\n"
        f"  Skipped        : {skipped}\n"
        f"  Errors         : {errors}\n"
    )

    if errors:
        sys.exit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migrate gst_mode field on org profiles.')
    parser.add_argument('--dry-run', action='store_true', help='Print what would change, do not write.')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
