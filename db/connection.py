# db/connection.py
# MongoDB client cache — one client per URI, reused across the process lifetime.

from pymongo import MongoClient
from pymongo.database import Database

from config.settings import settings

_clients: dict[str, MongoClient] = {}


def get_db(database: str = "diagnostics", uri: str | None = None) -> Database:
    """Return a MongoDB database handle, reusing one client per URI.

    Args:
        database: Database name to connect to.
        uri:      MongoDB connection URI. Defaults to settings.MONGO_URI.

    Returns:
        pymongo Database handle.
    """
    resolved_uri = uri or settings.MONGO_URI
    if resolved_uri not in _clients:
        _clients[resolved_uri] = MongoClient(resolved_uri)
    return _clients[resolved_uri][database]


if __name__ == "__main__":
    db = get_db()
    print("Connected to DB:", db.name)
    print("Collections:", db.list_collection_names())
