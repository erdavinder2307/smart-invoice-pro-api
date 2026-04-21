from datetime import datetime
import uuid
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from smart_invoice_pro.utils.cosmos_client import (
    customers_container,
    invoices_container,
    products_container,
    recently_viewed_container,
    search_history_container,
)
from smart_invoice_pro.utils.response_sanitizer import sanitize_item, sanitize_items


search_blueprint = Blueprint("search", __name__)


FEATURE_SEARCH_TARGETS = [
    {"title": "Invoices", "subtitle": "Create, edit and track invoices", "path": "/invoices", "entity_type": "feature"},
    {"title": "Customers", "subtitle": "Manage customer records", "path": "/customers", "entity_type": "feature"},
    {"title": "Products", "subtitle": "Manage catalog and stock-ready items", "path": "/products", "entity_type": "feature"},
    {"title": "Quotes", "subtitle": "Prepare and send quote proposals", "path": "/quotes", "entity_type": "feature"},
    {"title": "Sales Orders", "subtitle": "Track confirmed sales", "path": "/sales-orders", "entity_type": "feature"},
    {"title": "Vendors", "subtitle": "Manage vendor relationships", "path": "/vendors", "entity_type": "feature"},
    {"title": "Bills", "subtitle": "Track vendor bills", "path": "/bills", "entity_type": "feature"},
    {"title": "Expenses", "subtitle": "Capture and categorize expenses", "path": "/expenses", "entity_type": "feature"},
    {"title": "Reports", "subtitle": "Profit/loss, tax and cash reports", "path": "/reports", "entity_type": "feature"},
    {"title": "Settings", "subtitle": "Configure organization and billing defaults", "path": "/settings", "entity_type": "feature"},
]


def _parse_limit(default=5, max_limit=10):
    raw_limit = request.args.get("limit", default)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, max_limit))


def _history_to_result_item(item):
    query_text = item.get("query", "")
    item_type = item.get("type", "free_text")
    entity_id = item.get("entity_id")
    entity_type = item.get("entity_type")

    path = f"/search?q={quote(query_text)}"
    if item_type == "entity" and entity_type == "customer" and entity_id:
        path = f"/customers/{entity_id}"
    elif item_type == "entity" and entity_type == "invoice" and entity_id:
        path = f"/invoices/edit/{entity_id}"
    elif item_type == "entity" and entity_type == "product" and entity_id:
        path = f"/products/edit/{entity_id}"
    elif item_type == "feature" and item.get("path"):
        path = item.get("path")

    return {
        "id": item.get("id"),
        "query": query_text,
        "type": item_type,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "path": path,
        "created_at": item.get("created_at"),
    }


def _save_history(query_text, item_type="free_text", entity_id=None, entity_type=None, path=None):
    # Remove any existing entry for the same query+user to prevent duplicates (move-to-top)
    try:
        existing = list(search_history_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.user_id = @uid AND c.tenant_id = @tid "
                "AND c.query = @q"
            ),
            parameters=[
                {"name": "@uid", "value": request.user_id},
                {"name": "@tid", "value": request.tenant_id},
                {"name": "@q", "value": query_text},
            ],
            enable_cross_partition_query=True,
        ))
        for dup in existing:
            search_history_container.delete_item(item=dup["id"], partition_key=dup["user_id"])
    except Exception:
        pass  # non-fatal: proceed with insert regardless

    now = datetime.utcnow().isoformat()
    item = {
        "id": str(uuid.uuid4()),
        "user_id": request.user_id,
        "tenant_id": request.tenant_id,
        "query": query_text,
        "type": item_type,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "path": path,
        "created_at": now,
    }
    search_history_container.create_item(body=item)
    return sanitize_item(item)


def _fuzzy_score(needle, haystack):
    """Return a similarity score 0..1 between needle and haystack (case-insensitive)."""
    needle = needle.lower()
    haystack = haystack.lower()
    if needle in haystack:
        return 1.0
    # word-level: count how many needle words appear in haystack
    words = needle.split()
    if not words:
        return 0.0
    matched = sum(1 for w in words if w in haystack)
    word_score = matched / len(words)
    # character n-gram overlap (bigrams)
    def bigrams(s):
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else set()
    n_needle = bigrams(needle)
    n_haystack = bigrams(haystack)
    if n_needle:
        bigram_score = len(n_needle & n_haystack) / len(n_needle)
    else:
        bigram_score = 0.0
    return max(word_score, bigram_score * 0.8)


def _search_features(term, limit):
    needle = term.lower()
    scored = []
    for target in FEATURE_SEARCH_TARGETS:
        haystack = f"{target['title']} {target['subtitle']}"
        score = _fuzzy_score(needle, haystack)
        if score > 0.3:
            scored.append((score, target))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": target["title"].lower().replace(" ", "-"),
            "title": target["title"],
            "subtitle": target["subtitle"],
            "path": target["path"],
            "type": "feature",
            "entity_type": "feature",
        }
        for _, target in scored[:limit]
    ]


def _search_customers(term, limit):
    query = f"""
        SELECT TOP {limit} c.id, c.customer_id, c.display_name, c.email, c.phone
        FROM c
        WHERE c.tenant_id = @tenant_id
          AND (
            CONTAINS(LOWER(c.display_name), @term)
            OR CONTAINS(LOWER(c.email), @term)
            OR CONTAINS(LOWER(c.phone), @term)
          )
        ORDER BY c.updated_at DESC
    """
    params = [
        {"name": "@tenant_id", "value": request.tenant_id},
        {"name": "@term", "value": term.lower()},
    ]
    items = list(
        customers_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    sanitized = sanitize_items(items)
    return [
        {
            "id": item.get("id"),
            "title": item.get("display_name") or "Unnamed customer",
            "subtitle": item.get("email") or item.get("phone") or "Customer",
            "path": f"/customers/{item.get('id')}",
            "type": "entity",
            "entity_type": "customer",
            "entity_id": item.get("id"),
        }
        for item in sanitized
    ]


def _search_invoices(term, limit):
    query = f"""
        SELECT TOP {limit} c.id, c.invoice_number, c.customer_name, c.status, c.total_amount
        FROM c
        WHERE c.tenant_id = @tenant_id
          AND (
            CONTAINS(LOWER(c.invoice_number), @term)
            OR CONTAINS(LOWER(c.customer_name), @term)
            OR CONTAINS(LOWER(c.status), @term)
          )
        ORDER BY c.updated_at DESC
    """
    params = [
        {"name": "@tenant_id", "value": request.tenant_id},
        {"name": "@term", "value": term.lower()},
    ]
    items = list(
        invoices_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    sanitized = sanitize_items(items)
    return [
        {
            "id": item.get("id"),
            "title": item.get("invoice_number") or "Invoice",
            "subtitle": item.get("customer_name") or item.get("status") or "Invoice",
            "path": f"/invoices/edit/{item.get('id')}",
            "type": "entity",
            "entity_type": "invoice",
            "entity_id": item.get("id"),
        }
        for item in sanitized
    ]


def _search_products(term, limit):
    query = f"""
        SELECT TOP {limit} c.id, c.name, c.sku, c.selling_price, c.rate
        FROM c
        WHERE c.tenant_id = @tenant_id
          AND (
            CONTAINS(LOWER(c.name), @term)
            OR CONTAINS(LOWER(c.sku), @term)
          )
        ORDER BY c.updated_at DESC
    """
    params = [
        {"name": "@tenant_id", "value": request.tenant_id},
        {"name": "@term", "value": term.lower()},
    ]
    items = list(
        products_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    sanitized = sanitize_items(items)
    return [
        {
            "id": item.get("id"),
            "title": item.get("name") or "Product",
            "subtitle": item.get("sku") or f"Price: {item.get('selling_price') or item.get('rate') or '-'}",
            "path": f"/products/edit/{item.get('id')}",
            "type": "entity",
            "entity_type": "product",
            "entity_id": item.get("id"),
        }
        for item in sanitized
    ]


@search_blueprint.route("/search/history", methods=["GET"])
def list_search_history():
    limit = _parse_limit(default=5, max_limit=20)
    query = f"""
        SELECT TOP {limit} c.id, c.query, c.type, c.entity_id, c.entity_type, c.path, c.created_at
        FROM c
        WHERE c.user_id = @user_id AND c.tenant_id = @tenant_id
        ORDER BY c.created_at DESC
    """
    params = [
        {"name": "@user_id", "value": request.user_id},
        {"name": "@tenant_id", "value": request.tenant_id},
    ]
    items = list(
        search_history_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    sanitized = sanitize_items(items)
    return jsonify([_history_to_result_item(item) for item in sanitized]), 200


@search_blueprint.route("/search/history", methods=["POST"])
def create_search_history_item():
    data = request.get_json() or {}
    query_text = (data.get("query") or "").strip()
    if not query_text:
        return jsonify({"error": "query is required"}), 400

    item_type = data.get("type") or "free_text"
    entity_id = data.get("entity_id")
    entity_type = data.get("entity_type")
    path = data.get("path")

    item = _save_history(
        query_text=query_text,
        item_type=item_type,
        entity_id=entity_id,
        entity_type=entity_type,
        path=path,
    )
    return jsonify(_history_to_result_item(item)), 201


@search_blueprint.route("/search/history/<history_id>", methods=["DELETE"])
def delete_search_history_item(history_id):
    query = """
        SELECT TOP 1 c.id, c.user_id
        FROM c
        WHERE c.id = @id AND c.user_id = @user_id AND c.tenant_id = @tenant_id
    """
    params = [
        {"name": "@id", "value": history_id},
        {"name": "@user_id", "value": request.user_id},
        {"name": "@tenant_id", "value": request.tenant_id},
    ]
    items = list(
        search_history_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    if not items:
        return jsonify({"error": "Search history item not found"}), 404

    item = items[0]
    search_history_container.delete_item(item=item["id"], partition_key=item["user_id"])
    return jsonify({"message": "Deleted"}), 200


@search_blueprint.route("/search/history", methods=["DELETE"])
def clear_search_history():
    query = """
        SELECT c.id, c.user_id
        FROM c
        WHERE c.user_id = @user_id AND c.tenant_id = @tenant_id
    """
    params = [
        {"name": "@user_id", "value": request.user_id},
        {"name": "@tenant_id", "value": request.tenant_id},
    ]
    items = list(
        search_history_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    for item in items:
        search_history_container.delete_item(item=item["id"], partition_key=item["user_id"])

    return jsonify({"message": "Search history cleared", "deleted": len(items)}), 200


@search_blueprint.route("/search", methods=["GET"])
def global_search():
    raw_query = request.args.get("q", "")
    term = raw_query.strip()
    if not term:
        return jsonify({"query": "", "results": {"features": [], "customers": [], "invoices": [], "products": []}, "total": 0}), 200

    per_category_limit = _parse_limit(default=5, max_limit=20)

    features = _search_features(term, per_category_limit)
    customers = _search_customers(term, per_category_limit)
    invoices = _search_invoices(term, per_category_limit)
    products = _search_products(term, per_category_limit)

    total = len(features) + len(customers) + len(invoices) + len(products)

    return jsonify(
        {
            "query": term,
            "results": {
                "features": features,
                "customers": customers,
                "invoices": invoices,
                "products": products,
            },
            "total": total,
        }
    ), 200


# ---------------------------------------------------------------------------
# Recently Viewed
# ---------------------------------------------------------------------------

@search_blueprint.route("/search/recently-viewed", methods=["GET"])
def list_recently_viewed():
    limit = _parse_limit(default=5, max_limit=20)
    query = f"""
        SELECT TOP {limit} c.id, c.entity_id, c.entity_type, c.title, c.subtitle,
               c.path, c.viewed_at
        FROM c
        WHERE c.user_id = @user_id AND c.tenant_id = @tenant_id
        ORDER BY c.viewed_at DESC
    """
    params = [
        {"name": "@user_id", "value": request.user_id},
        {"name": "@tenant_id", "value": request.tenant_id},
    ]
    items = list(
        recently_viewed_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    return jsonify(sanitize_items(items)), 200


@search_blueprint.route("/search/recently-viewed", methods=["POST"])
def track_recently_viewed():
    data = request.get_json() or {}
    entity_id = (data.get("entity_id") or "").strip()
    entity_type = (data.get("entity_type") or "").strip()
    title = (data.get("title") or "").strip()
    if not entity_id or not entity_type:
        return jsonify({"error": "entity_id and entity_type are required"}), 400

    # Upsert: remove existing entry for same entity+user to avoid duplicates
    try:
        existing = list(recently_viewed_container.query_items(
            query=(
                "SELECT * FROM c WHERE c.user_id = @uid AND c.tenant_id = @tid "
                "AND c.entity_id = @eid AND c.entity_type = @etype"
            ),
            parameters=[
                {"name": "@uid", "value": request.user_id},
                {"name": "@tid", "value": request.tenant_id},
                {"name": "@eid", "value": entity_id},
                {"name": "@etype", "value": entity_type},
            ],
            enable_cross_partition_query=True,
        ))
        for dup in existing:
            recently_viewed_container.delete_item(item=dup["id"], partition_key=dup["user_id"])
    except Exception:
        pass

    path = data.get("path") or ""
    if not path:
        if entity_type == "customer":
            path = f"/customers/{entity_id}"
        elif entity_type == "invoice":
            path = f"/invoices/edit/{entity_id}"
        elif entity_type == "product":
            path = f"/products/edit/{entity_id}"

    item = {
        "id": str(uuid.uuid4()),
        "user_id": request.user_id,
        "tenant_id": request.tenant_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "title": title,
        "subtitle": data.get("subtitle") or entity_type.capitalize(),
        "path": path,
        "viewed_at": datetime.utcnow().isoformat(),
    }
    recently_viewed_container.create_item(body=item)
    return jsonify(sanitize_item(item)), 201


@search_blueprint.route("/search/recently-viewed/<item_id>", methods=["DELETE"])
def delete_recently_viewed_item(item_id):
    items = list(recently_viewed_container.query_items(
        query=(
            "SELECT TOP 1 c.id, c.user_id FROM c "
            "WHERE c.id = @id AND c.user_id = @uid AND c.tenant_id = @tid"
        ),
        parameters=[
            {"name": "@id", "value": item_id},
            {"name": "@uid", "value": request.user_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    if not items:
        return jsonify({"error": "Not found"}), 404
    recently_viewed_container.delete_item(item=items[0]["id"], partition_key=items[0]["user_id"])
    return jsonify({"message": "Deleted"}), 200


@search_blueprint.route("/search/recently-viewed", methods=["DELETE"])
def clear_recently_viewed():
    items = list(recently_viewed_container.query_items(
        query=(
            "SELECT c.id, c.user_id FROM c "
            "WHERE c.user_id = @uid AND c.tenant_id = @tid"
        ),
        parameters=[
            {"name": "@uid", "value": request.user_id},
            {"name": "@tid", "value": request.tenant_id},
        ],
        enable_cross_partition_query=True,
    ))
    for item in items:
        recently_viewed_container.delete_item(item=item["id"], partition_key=item["user_id"])
    return jsonify({"message": "Cleared", "deleted": len(items)}), 200
