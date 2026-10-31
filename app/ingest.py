"""Set up the database and index the help docs: python -m app.ingest"""
import os

from .config import BASE_DIR, load_settings
from .docs_index import ingest
from .llm import GeminiEmbedder
from .store import Store


def main():
    settings = load_settings()
    store = Store(settings["database_url"])
    store.init_schema()
    demo_password = os.environ.get("DEMO_PASSWORD")
    if demo_password:
        store.seed(demo_password)
    count = ingest(store, GeminiEmbedder(settings["gemini_api_key"]), BASE_DIR / "data" / "docs")
    print(f"Embedded {count} changed passage(s).")


if __name__ == "__main__":
    main()
