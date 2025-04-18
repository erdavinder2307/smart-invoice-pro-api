import os
from azure.cosmos import CosmosClient, PartitionKey
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("COSMOS_URI")
key = os.getenv("COSMOS_KEY")
database_name = os.getenv("COSMOS_DB_NAME")
container_name = os.getenv("COSMOS_CONTAINER_NAME")

client = CosmosClient(uri, credential=key)
database = client.create_database_if_not_exists(id=database_name)
container = database.create_container_if_not_exists(
    id=container_name,
    partition_key=PartitionKey(path="/customer_id"),
    offer_throughput=400
)
