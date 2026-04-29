import os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
from smart_invoice_pro.utils.cosmos_client import users_container

results = list(users_container.query_items(
    query="SELECT c.id, c.tenant_id, c.email, c.username, c.role FROM c WHERE c.email = @email",
    parameters=[{"name": "@email", "value": "admin@solidevelectrosoft.com"}],
    enable_cross_partition_query=True,
))
if not results:
    print("No user found — trying case-insensitive scan...")
    all_users = list(users_container.query_items(
        query="SELECT c.id, c.tenant_id, c.email FROM c",
        enable_cross_partition_query=True,
    ))
    for u in all_users:
        if "solidevelectrosoft" in u.get("email", "").lower():
            print(u)
else:
    for r in results:
        print(r)
