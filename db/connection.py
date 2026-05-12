# db/connection.py
# MongoDB client cache.

import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

_clients: dict[str, MongoClient] = {}


def get_db(database: str = "diagnostics", uri: str | None = None):
    """Return a MongoDB database handle, reusing one client per URI."""
    resolved_uri = uri or os.getenv("MONGO_URI", "mongodb://localhost:27017")
    if resolved_uri not in _clients:
        _clients[resolved_uri] = MongoClient(resolved_uri)
    _client = _clients[resolved_uri]
    return _client[database]
