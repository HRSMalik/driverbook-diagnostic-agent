# db/connection.py
# MongoDB client singleton.

import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

_client: MongoClient | None = None


def get_db(database: str = "diagnostics"):
    """Return a MongoDB database handle, reusing a single global client."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        _client = MongoClient(uri)
    return _client[database]
