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
    try:
        # Try to get the container if it exists
        return database.get_container_client(container_name)
    except exceptions.CosmosResourceNotFoundError:
        # If not found, create it with the correct partition key
        return database.create_container(
            id=container_name,
            partition_key=PartitionKey(path=partition_key),
            offer_throughput=400
        )

users_container = get_container("users", "/userid")
invoices_container = get_container("invoices", "/customer_id")
customers_container = get_container("customers", "/customer_id")
