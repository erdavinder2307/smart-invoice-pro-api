"""
Seed plan definitions into the plans_container.
Run once after deploying the subscription schema:

    cd smart-invoice-pro-api-2
    python scripts/seed_plans.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from smart_invoice_pro.utils.cosmos_client import plans_container

PLANS = [
    {
        "id": "starter_monthly",
        "name": "Starter",
        "slug": "starter",
        "billing_cycle": "monthly",
        "is_public": True,
        "is_active": True,
        "sort_order": 1,
        "pricing": {
            "inr_monthly": 599,
            "inr_annual": 5999,
            "usd_monthly": 7,
            "usd_annual": 72,
        },
        "trial_days": 0,
        "features": {
            "max_users": 3,
            "invoices_per_month": 100,
            "quotes_per_month": 50,
            "purchase_orders_per_month": 50,
            "bills_per_month": 50,
            "expenses_per_month": 100,
            "max_products": 500,
            "api_access": False,
            "automation_rules": 0,
            "webhooks": 0,
            "advanced_reports": False,
            "bank_reconciliation": True,
            "custom_domain": False,
            "customer_portal": True,
            "branding_customization": True,
            "multi_gstin": False,
            "priority_support": False,
            "api_calls_per_month": 0,
        },
        "display_features": [
            "3 users included",
            "100 invoices/month",
            "50 quotes/month",
            "GST reports",
            "Bank reconciliation",
            "Customer portal",
            "Email support (72h SLA)",
        ],
        "support_level": "email",
        "created_at": "2026-06-24T00:00:00+00:00",
    },
    {
        "id": "starter_annual",
        "name": "Starter",
        "slug": "starter",
        "billing_cycle": "annual",
        "is_public": True,
        "is_active": True,
        "sort_order": 2,
        "pricing": {
            "inr_monthly": 499,
            "inr_annual": 5999,
            "usd_monthly": 6,
            "usd_annual": 72,
        },
        "trial_days": 0,
        "features": {
            "max_users": 3,
            "invoices_per_month": 100,
            "quotes_per_month": 50,
            "purchase_orders_per_month": 50,
            "bills_per_month": 50,
            "expenses_per_month": 100,
            "max_products": 500,
            "api_access": False,
            "automation_rules": 0,
            "webhooks": 0,
            "advanced_reports": False,
            "bank_reconciliation": True,
            "custom_domain": False,
            "customer_portal": True,
            "branding_customization": True,
            "multi_gstin": False,
            "priority_support": False,
            "api_calls_per_month": 0,
        },
        "display_features": [
            "3 users included",
            "100 invoices/month",
            "50 quotes/month",
            "GST reports",
            "Bank reconciliation",
            "Customer portal",
            "Email support (72h SLA)",
            "Save 17% vs monthly",
        ],
        "support_level": "email",
        "created_at": "2026-06-24T00:00:00+00:00",
    },
    {
        "id": "growth_5u_monthly",
        "name": "Growth",
        "slug": "growth",
        "billing_cycle": "monthly",
        "is_public": False,
        "is_active": True,
        "sort_order": 3,
        "pricing": {
            "inr_monthly": 1499,
            "inr_annual": 14999,
            "usd_monthly": 18,
            "usd_annual": 180,
        },
        "trial_days": 0,
        "features": {
            "max_users": 5,
            "invoices_per_month": -1,
            "quotes_per_month": -1,
            "purchase_orders_per_month": -1,
            "bills_per_month": -1,
            "expenses_per_month": -1,
            "max_products": -1,
            "api_access": True,
            "automation_rules": 5,
            "webhooks": 3,
            "advanced_reports": True,
            "bank_reconciliation": True,
            "custom_domain": False,
            "customer_portal": True,
            "branding_customization": True,
            "multi_gstin": False,
            "priority_support": True,
            "api_calls_per_month": 10000,
        },
        "display_features": [
            "5 users included",
            "Unlimited invoices",
            "API access (10,000 calls/month)",
            "5 automation rules",
            "Advanced reports",
            "Bank reconciliation",
            "Priority support (24h SLA)",
        ],
        "support_level": "priority",
        "created_at": "2026-06-24T00:00:00+00:00",
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "slug": "enterprise",
        "billing_cycle": "custom",
        "is_public": False,
        "is_active": True,
        "sort_order": 4,
        "pricing": {
            "inr_monthly": None,
            "inr_annual": None,
            "usd_monthly": None,
            "usd_annual": None,
        },
        "trial_days": 0,
        "features": {
            "max_users": -1,
            "invoices_per_month": -1,
            "quotes_per_month": -1,
            "purchase_orders_per_month": -1,
            "bills_per_month": -1,
            "expenses_per_month": -1,
            "max_products": -1,
            "api_access": True,
            "automation_rules": -1,
            "webhooks": -1,
            "advanced_reports": True,
            "bank_reconciliation": True,
            "custom_domain": True,
            "customer_portal": True,
            "branding_customization": True,
            "multi_gstin": True,
            "priority_support": True,
            "api_calls_per_month": -1,
        },
        "display_features": [
            "Custom user seats",
            "Unlimited everything",
            "Custom domain",
            "Multi-GSTIN",
            "Dedicated CSM",
            "SLA-backed support",
            "White-glove onboarding",
        ],
        "support_level": "dedicated",
        "created_at": "2026-06-24T00:00:00+00:00",
    },
]


def seed():
    print("Seeding plan definitions...")
    for plan in PLANS:
        try:
            plans_container.upsert_item(body=plan)
            print(f"  ✓ {plan['id']} ({plan['name']} / {plan['billing_cycle']})")
        except Exception as e:
            print(f"  ✗ {plan['id']}: {e}")
    print("Done.")


if __name__ == "__main__":
    seed()
