# db/connection.py
# MongoDB client cache — one client per URI, reused across the process lifetime.

import os

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()

_clients: dict[str, MongoClient] = {}


def get_db(database: str = "diagnostics", uri: str | None = None) -> Database:
    """Return a MongoDB database handle, reusing one client per URI.

    Args:
        database: Database name to connect to.
        uri:      MongoDB connection URI. Defaults to MONGO_URI env var.

    Returns:
        pymongo Database handle.
    """
    resolved_uri = uri or os.getenv("MONGO_URI", "mongodb://localhost:27017")
    if resolved_uri not in _clients:
        _clients[resolved_uri] = MongoClient(resolved_uri)
    return _clients[resolved_uri][database]


if __name__ == "__main__":
    db = get_db()
    print("Connected to DB:", db.name)
    print("Collections:", db.list_collection_names())
