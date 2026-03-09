"""
Script to create an admin user manually.
Usage:
    cd /Users/davinderpal/Development/invoicing/smart-invoice-pro-api-2
    source venv/bin/activate
    python create_admin_user.py
"""

import os
import uuid
from datetime import datetime
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, PartitionKey

# Load environment variables
load_dotenv()

# Admin credentials
ADMIN_USERNAME = "admin@solidevelectrosoft.com"
ADMIN_PASSWORD = "Solidev@2026"

# Cosmos DB connection
uri = os.getenv("COSMOS_URI")
key = os.getenv("COSMOS_KEY")
database_name = os.getenv("COSMOS_DB_NAME")

if not all([uri, key, database_name]):
    print("❌ Error: Missing Cosmos DB environment variables")
    print("   Make sure COSMOS_URI, COSMOS_KEY, and COSMOS_DB_NAME are set in .env")
    exit(1)

# Connect to Cosmos DB
client = CosmosClient(uri, credential=key)
database = client.create_database_if_not_exists(id=database_name)

# Get or create users container with correct partition key
users_container = database.create_container_if_not_exists(
    id="users",
    partition_key=PartitionKey(path="/userid")
)

# Check if admin already exists
existing_query = f"SELECT * FROM c WHERE c.username = '{ADMIN_USERNAME}'"
existing_users = list(users_container.query_items(
    query=existing_query,
    enable_cross_partition_query=True
))

if existing_users:
    print(f"⚠️  User '{ADMIN_USERNAME}' already exists!")
    print(f"   User ID: {existing_users[0]['id']}")
    print(f"   Role: {existing_users[0].get('role', 'N/A')}")
    
    # Ask if user wants to update
    choice = input("\n   Do you want to update the password? (yes/no): ").strip().lower()
    if choice == 'yes':
        user = existing_users[0]
        user['password'] = generate_password_hash(ADMIN_PASSWORD, method='pbkdf2:sha256', salt_length=16)
        user['role'] = 'Admin'
        user['updated_at'] = datetime.utcnow().isoformat()
        users_container.upsert_item(body=user)
        print(f"✅ Password updated successfully for '{ADMIN_USERNAME}'")
    else:
        print("   No changes made.")
    exit(0)

# Create new admin user
user_id = str(uuid.uuid4())
hashed_password = generate_password_hash(ADMIN_PASSWORD, method='pbkdf2:sha256', salt_length=16)

admin_user = {
    'id': user_id,
    'userid': user_id,  # partition key field
    'username': ADMIN_USERNAME,
    'password': hashed_password,
    'role': 'Admin',
    'created_at': datetime.utcnow().isoformat()
}

try:
    users_container.create_item(body=admin_user)
    print("✅ Admin user created successfully!")
    print(f"   Username: {ADMIN_USERNAME}")
    print(f"   Password: {ADMIN_PASSWORD}")
    print(f"   User ID:  {user_id}")
    print(f"   Role:     Admin")
    print("\n🎉 You can now login with these credentials!")
except Exception as e:
    print(f"❌ Error creating admin user: {e}")
    exit(1)
