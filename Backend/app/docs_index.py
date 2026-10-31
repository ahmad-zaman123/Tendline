import hashlib
import re
from pathlib import Path

from pgvector import Vector

SEARCH_LIMIT = 3
MIN_SIMILARITY = 0.62
# Part numbers and similar codes. Matching ordinary words like "take" only added wrong passages.
IDENTIFIER = re.compile(r"\b(?=[\w-]*\d)[\w-]{3,}\b")


def split_markdown(path):
    """Split a markdown file into one passage per heading."""
    passages = []
    heading, lines = path.stem, []

    def flush():
        body = "\n".join(lines).strip()
        if body:
            passages.append({"source": f"{path.name}#{heading}", "text": f"{heading}\n{body}"})

    for line in path.read_text().splitlines():
        if line.startswith("#"):
            flush()
            heading, lines = line.lstrip("# ").strip(), []
        else:
            lines.append(line)
    flush()
    return passages


def ingest(store, embedder, docs_dir):
    """Embed new or changed passages and remove deleted ones. Returns how many were embedded."""
    passages = [p for path in sorted(Path(docs_dir).glob("*.md")) for p in split_markdown(path)]
    existing = store.passage_hashes()
    changed = []
    for passage in passages:
        passage["hash"] = hashlib.sha256(passage["text"].encode()).hexdigest()
        if existing.get(passage["source"]) != passage["hash"]:
            changed.append(passage)
    if changed:
        embeddings = embedder.embed_documents([p["text"] for p in changed])
        for passage, embedding in zip(changed, embeddings):
            store.upsert_passage(
                passage["source"], passage["text"], passage["hash"], Vector(embedding)
            )
    store.delete_passages_except([p["source"] for p in passages])
    return len(changed)


class DocsIndex:
    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder

    def search(self, query):
        embedding = Vector(self.embedder.embed_query(query))
        identifiers = " ".join(IDENTIFIER.findall(query))
        return self.store.search_passages(embedding, identifiers, SEARCH_LIMIT, MIN_SIMILARITY)
