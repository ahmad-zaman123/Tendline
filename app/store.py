import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

EMBEDDING_DIMENSIONS = 768
MAX_FAILED_LOGINS = 5
RESOLVED_EVENT = "The support team closed this request. The AI assistant is back."
LOGIN_LOCK = timedelta(minutes=15)

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('customer', 'staff')),
    name TEXT NOT NULL,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL REFERENCES users(email),
    item TEXT NOT NULL,
    status TEXT NOT NULL,
    placed_on DATE NOT NULL,
    tracking_number TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    customer_email TEXT NOT NULL REFERENCES users(email),
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages (conversation_id, id);
CREATE TABLE IF NOT EXISTS tool_calls (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    name TEXT NOT NULL,
    args JSONB NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tool_calls_conversation_idx ON tool_calls (conversation_id, id);
CREATE TABLE IF NOT EXISTS escalations (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS doc_passages (
    source TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    embedding vector({EMBEDDING_DIMENSIONS}) NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_passages_embedding_idx
    ON doc_passages USING hnsw (embedding vector_cosine_ops);
ALTER TABLE doc_passages ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
CREATE INDEX IF NOT EXISTS doc_passages_search_idx ON doc_passages USING gin (search_vector);
"""

# Vector search can miss exact codes like part numbers, so %(query)s holds only those codes.
# Reciprocal rank fusion merges both lists by position, since their scores aren't comparable.
HYBRID_SEARCH = """
WITH nearest AS (
    SELECT source, 1 - (embedding <=> %(embedding)s) AS similarity
    FROM doc_passages ORDER BY embedding <=> %(embedding)s LIMIT %(candidates)s
), semantic AS (
    SELECT source, row_number() OVER (ORDER BY similarity DESC) AS position
    FROM nearest WHERE similarity >= %(min_similarity)s
), terms AS (
    SELECT replace(plainto_tsquery('english', %(query)s)::text, '&', '|')::tsquery AS query
), keyword AS (
    SELECT source, row_number() OVER (ORDER BY ts_rank(search_vector, terms.query) DESC) AS position
    FROM doc_passages, terms WHERE search_vector @@ terms.query LIMIT %(candidates)s
), fused AS (
    SELECT source, sum(1.0 / (60 + position)) AS score
    FROM (SELECT * FROM semantic UNION ALL SELECT * FROM keyword) ranked GROUP BY source
)
SELECT p.source, p.text FROM fused JOIN doc_passages p USING (source)
ORDER BY fused.score DESC LIMIT %(limit)s
"""

SEED_USERS = [
    ("sara@example.com", "customer", "Sara"),
    ("omar@example.com", "customer", "Omar"),
    ("agent@example.com", "staff", "Support Agent"),
]
SEED_ORDERS = [
    ("A1001", "sara@example.com", "Standing desk", "shipped", "2026-10-20", "TRK-55821"),
    ("A1002", "sara@example.com", "Monitor arm", "processing", "2026-10-28", None),
    ("B2001", "omar@example.com", "Office chair", "delivered", "2026-09-30", "TRK-41007"),
]


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password, stored):
    salt, digest = stored.split(":")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


# Compared against when the email is unknown, so response time doesn't reveal which accounts exist.
_DUMMY_HASH = hash_password(uuid.uuid4().hex)


class Store:
    def __init__(self, database_url):
        self.database_url = database_url
        self._pool = None

    def _configure_connection(self, db):
        register_vector(db)
        db.commit()

    def _get_pool(self):
        # Opened on first use because the vector type can't load until init_schema has run.
        if self._pool is None:
            self._pool = ConnectionPool(
                self.database_url,
                open=False,
                min_size=1,
                max_size=5,
                # Neon closes idle connections when it scales to zero.
                check=ConnectionPool.check_connection,
                configure=self._configure_connection,
                # PgBouncer-style poolers share server connections across clients.
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
            )
            self._pool.open(wait=True, timeout=30)
        return self._pool

    def close(self):
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def init_schema(self):
        with psycopg.connect(self.database_url, autocommit=True) as db:
            db.execute(SCHEMA)

    def seed(self, demo_password):
        """Create demo accounts and orders. Existing rows are left alone."""
        with self._connect() as db:
            for email, role, name in SEED_USERS:
                db.execute(
                    "INSERT INTO users (email, password_hash, role, name) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (email) DO NOTHING",
                    (email, hash_password(demo_password), role, name),
                )
            for order in SEED_ORDERS:
                db.execute(
                    "INSERT INTO orders VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    order,
                )

    @contextmanager
    def _connect(self):
        with self._get_pool().connection() as db:
            yield db

    def authenticate(self, email, password):
        """Return the user, or None for a wrong password or a locked account."""
        with self._connect() as db:
            user = db.execute("SELECT * FROM users WHERE email = %s FOR UPDATE", (email,)).fetchone()
            if user is None:
                verify_password(password, _DUMMY_HASH)
                return None
            locked = user["locked_until"] and user["locked_until"] > datetime.now(timezone.utc)
            if verify_password(password, user["password_hash"]) and not locked:
                db.execute(
                    "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE email = %s", (email,)
                )
                return {"email": user["email"], "role": user["role"], "name": user["name"]}
            if not locked:
                failures = user["failed_logins"] + 1
                lock_until = None
                if failures >= MAX_FAILED_LOGINS:
                    failures = 0
                    lock_until = datetime.now(timezone.utc) + LOGIN_LOCK
                db.execute(
                    "UPDATE users SET failed_logins = %s, locked_until = %s WHERE email = %s",
                    (failures, lock_until, email),
                )
            return None

    def get_user(self, email):
        with self._connect() as db:
            return db.execute(
                "SELECT email, role, name FROM users WHERE email = %s", (email,)
            ).fetchone()

    def get_order(self, order_id, customer_email):
        with self._connect() as db:
            row = db.execute(
                "SELECT id, item, status, placed_on::text, tracking_number FROM orders "
                "WHERE id = %s AND customer_email = %s",
                (order_id, customer_email),
            ).fetchone()
        return row

    def list_orders(self, customer_email):
        with self._connect() as db:
            return db.execute(
                "SELECT id, item, status, placed_on::text, tracking_number FROM orders "
                "WHERE customer_email = %s ORDER BY placed_on DESC",
                (customer_email,),
            ).fetchall()

    def create_conversation(self, customer_email):
        conversation_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO conversations (id, customer_email) VALUES (%s, %s)",
                (conversation_id, customer_email),
            )
        return conversation_id

    def get_conversation(self, conversation_id, customer_email=None):
        query = "SELECT * FROM conversations WHERE id = %s"
        params = [conversation_id]
        if customer_email is not None:
            query += " AND customer_email = %s"
            params.append(customer_email)
        with self._connect() as db:
            return db.execute(query, params).fetchone()

    def latest_conversation(self, customer_email):
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM conversations WHERE customer_email = %s ORDER BY created_at DESC LIMIT 1",
                (customer_email,),
            ).fetchone()

    def add_message(self, conversation_id, role, text, sources=()):
        with self._connect() as db:
            db.execute(
                "INSERT INTO messages (conversation_id, role, text, sources) VALUES (%s, %s, %s, %s)",
                (conversation_id, role, text, json.dumps(list(sources))),
            )

    def get_messages(self, conversation_id):
        with self._connect() as db:
            return db.execute(
                "SELECT role, text, sources FROM messages WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            ).fetchall()

    def log_tool_call(self, conversation_id, name, args, result):
        with self._connect() as db:
            db.execute(
                "INSERT INTO tool_calls (conversation_id, name, args, result) VALUES (%s, %s, %s, %s)",
                (conversation_id, name, json.dumps(args), json.dumps(result)),
            )

    def get_tool_calls(self, conversation_id):
        with self._connect() as db:
            return db.execute(
                "SELECT name, args, result FROM tool_calls WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            ).fetchall()

    def escalate(self, conversation_id, reason, summary, reply):
        """Open the escalation and add the hand-off reply in one transaction, so staff never see one without the other."""
        with self._connect() as db:
            db.execute(
                "INSERT INTO messages (conversation_id, role, text, sources) VALUES (%s, 'assistant', %s, '[]')",
                (conversation_id, reply),
            )
            db.execute(
                "INSERT INTO escalations (conversation_id, reason, summary) VALUES (%s, %s, %s)",
                (conversation_id, reason, summary),
            )
            db.execute("UPDATE conversations SET status = 'escalated' WHERE id = %s", (conversation_id,))

    def list_escalations(self):
        with self._connect() as db:
            return db.execute(
                "SELECT e.id, e.conversation_id, e.reason, e.summary, e.created_at, c.customer_email, "
                "(SELECT m.role FROM messages m WHERE m.conversation_id = e.conversation_id "
                " ORDER BY m.id DESC LIMIT 1) AS last_role "
                "FROM escalations e JOIN conversations c ON c.id = e.conversation_id "
                "WHERE e.status = 'open' ORDER BY e.id"
            ).fetchall()

    def resolve_escalation(self, escalation_id):
        """Close an escalation and give the conversation back to the agent."""
        with self._connect() as db:
            row = db.execute(
                "UPDATE escalations SET status = 'resolved' WHERE id = %s AND status = 'open' "
                "RETURNING conversation_id",
                (escalation_id,),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "UPDATE conversations SET status = 'open' WHERE id = %s", (row["conversation_id"],)
            )
            db.execute(
                "INSERT INTO messages (conversation_id, role, text) VALUES (%s, 'event', %s)",
                (row["conversation_id"], RESOLVED_EVENT),
            )
        return True

    def passage_hashes(self):
        with self._connect() as db:
            rows = db.execute("SELECT source, text_hash FROM doc_passages").fetchall()
        return {row["source"]: row["text_hash"] for row in rows}

    def upsert_passage(self, source, text, text_hash, embedding):
        with self._connect() as db:
            db.execute(
                "INSERT INTO doc_passages (source, text, text_hash, embedding) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (source) DO UPDATE SET text = EXCLUDED.text, "
                "text_hash = EXCLUDED.text_hash, embedding = EXCLUDED.embedding",
                (source, text, text_hash, embedding),
            )

    def delete_passages_except(self, sources):
        with self._connect() as db:
            db.execute("DELETE FROM doc_passages WHERE NOT (source = ANY(%s))", (list(sources),))

    def search_passages(self, embedding, query, limit, min_similarity):
        with self._connect() as db:
            return db.execute(
                HYBRID_SEARCH,
                {
                    "embedding": embedding,
                    "query": query,
                    "limit": limit,
                    "candidates": limit * 4,
                    "min_similarity": min_similarity,
                },
            ).fetchall()
