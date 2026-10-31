"""Start a local Postgres with pgvector and print its URL: python scripts/dev_db.py"""
from pathlib import Path

import pgserver

server = pgserver.get_server(Path(__file__).resolve().parent.parent / ".pgdata", cleanup_mode=None)
print(server.get_uri())
