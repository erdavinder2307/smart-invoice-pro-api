import os
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("COSMOS_URI")
key = os.getenv("COSMOS_KEY")
database_name = os.getenv("COSMOS_DB_NAME")

client = CosmosClient(uri, credential=key)
database = client.create_database_if_not_exists(id=database_name)

def get_container(container_name, partition_key):
    # Create container if it doesn't exist, or get existing container
    # Note: offer_throughput is not supported for serverless accounts
    return database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path=partition_key)
    )

users_container = get_container("users", "/userid")
invoices_container = get_container("invoices", "/customer_id")
customers_container = get_container("customers", "/customer_id")
products_container = get_container("products", "/product_id")
stock_container = get_container("stock", "/product_id")
bank_accounts_container = get_container("bank_accounts", "/user_id")

