import hashlib
import json
import math
import re
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app import mailer
from app.agent import HANDOFF_REPLY, MAX_STEPS
from app.config import BASE_DIR
from app.docs_index import DocsIndex, ingest
from app.llm import GeminiClient, LLMReply, ToolCall
from app.main import RATE_LIMITS, create_app
from app.store import EMBEDDING_DIMENSIONS, MAX_FAILED_LOGINS, Store

SECRET = "test-secret-that-is-long-enough-for-hs256"
PASSWORD = "demo-password"
WARRANTY_TEXT = (
    "Warranty\nDesks and chairs have a 5-year warranty covering manufacturing faults. "
    "Accessories have a 1-year warranty. Normal wear and accidental damage are not covered."
)


class ScriptedLLM:
    def __init__(self):
        self.replies = []
        self.histories = []

    def complete(self, system, history, tools):
        self.histories.append(list(history))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class HashEmbedder:
    """Deterministic bag-of-words embedding, so similar text gets similar vectors."""

    calls = 0

    def _vector(self, text):
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"[a-z]+", text.lower()):
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        HashEmbedder.calls += len(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    import pgserver

    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def store(database_url):
    store = Store(database_url)
    store.init_schema()
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute(
            "TRUNCATE users, orders, conversations, messages, tool_calls, escalations, doc_passages, "
            "rate_limits CASCADE"
        )
    store.seed(PASSWORD)
    ingest(store, HashEmbedder(), BASE_DIR / "data" / "docs")
    yield store
    store.close()


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def client(store, llm):
    return TestClient(
        create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, demo_mode=True)
    )


def login(client, email):
    response = client.post("/api/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def sara(client):
    return login(client, "sara@example.com")


@pytest.fixture
def omar(client):
    return login(client, "omar@example.com")


@pytest.fixture
def staff(client):
    return login(client, "agent@example.com")


def tool(name, **args):
    return LLMReply(tool_calls=[ToolCall(name, args)], raw={"call": name})


def gemini_with(*outcomes):
    """A real GeminiClient whose API call returns or raises the given outcomes in order."""
    calls = []
    remaining = list(outcomes)

    def generate_content(model, contents, config):
        calls.append(model)
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    gemini = GeminiClient("test-key", ["primary", "fallback"])
    gemini.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return gemini, calls


GEMINI_OK = SimpleNamespace(function_calls=None, text="Hi.", candidates=[SimpleNamespace(content="raw")])
USER_TURN = [{"role": "user", "text": "Hello"}]


@pytest.mark.parametrize(
    "failure",
    [
        errors.ServerError(503, {"error": {"message": "overloaded"}}),
        errors.ServerError(504, {"error": {"message": "deadline"}}),
        errors.ClientError(429, {"error": {"message": "rate limit"}}),
        errors.ClientError(404, {"error": {"message": "model retired"}}),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_temporary_model_failures_use_the_fallback(failure):
    gemini, calls = gemini_with(failure, GEMINI_OK)
    assert gemini.complete("system", USER_TURN, []).text == "Hi."
    assert calls == ["primary", "fallback"]


@pytest.mark.parametrize("code", [400, 401, 403])
def test_problems_every_model_would_share_skip_the_fallback(code):
    gemini, calls = gemini_with(errors.ClientError(code, {"error": {"message": "bad"}}))
    with pytest.raises(errors.ClientError):
        gemini.complete("system", USER_TURN, [])
    assert calls == ["primary"]


def test_every_model_is_tried_in_order():
    down = errors.ClientError(429, {"error": {"message": "rate limit"}})
    gemini, calls = gemini_with(down, down, down, GEMINI_OK)
    gemini.models = ["first", "second", "third", "fourth"]
    assert gemini.complete("system", USER_TURN, []).text == "Hi."
    assert calls == ["first", "second", "third", "fourth"]


def test_no_new_model_starts_after_the_deadline(monkeypatch):
    clock = iter([0, 21])
    monkeypatch.setattr("app.llm.time.monotonic", lambda: next(clock))
    gemini, calls = gemini_with(httpx.ReadTimeout("timed out"), GEMINI_OK)
    with pytest.raises(httpx.ReadTimeout):
        gemini.complete("system", USER_TURN, [])
    assert calls == ["primary"]


def test_healthy_primary_never_calls_the_fallback():
    gemini, calls = gemini_with(GEMINI_OK)
    gemini.complete("system", USER_TURN, [])
    assert calls == ["primary"]


def test_all_models_failing_reaches_the_agent_handoff():
    last = errors.ServerError(503, {"error": {"message": "fallback down too"}})
    gemini, calls = gemini_with(errors.ServerError(503, {"error": {"message": "down"}}), last)
    with pytest.raises(errors.ServerError) as raised:
        gemini.complete("system", USER_TURN, [])
    assert raised.value is last
    assert calls == ["primary", "fallback"]


def test_keyword_search_finds_exact_part_numbers(store):
    docs = DocsIndex(store, HashEmbedder())
    assert docs.search("NW-4471")[0]["source"] == "policies.md#Recalled parts"


def test_part_number_inside_a_sentence_is_found(store):
    docs = DocsIndex(store, HashEmbedder())
    assert docs.search("is part NW-4471 safe to use?")[0]["source"] == "policies.md#Recalled parts"


def test_ordinary_words_do_not_trigger_keyword_matches(store):
    # "take" and "back" appear in the docs, and these fake embeddings score below the cutoff.
    assert DocsIndex(store, HashEmbedder()).search("do you take money back") == []


def test_vector_search_still_finds_passages_without_keyword_overlap(store):
    class FixedEmbedder(HashEmbedder):
        def embed_query(self, text):
            return HashEmbedder().embed_query(WARRANTY_TEXT)

    results = DocsIndex(store, FixedEmbedder()).search("zzz")
    assert [r["source"] for r in results] == ["policies.md#Warranty"]


def test_search_text_is_passed_as_data(store):
    assert isinstance(DocsIndex(store, HashEmbedder()).search("'; DROP TABLE users; --"), list)
    assert store.get_user("sara@example.com") is not None


def test_requests_need_a_valid_login(client):
    assert client.post("/api/chat", json={"message": "Hi"}).status_code == 401
    bad = {"Authorization": "Bearer nonsense"}
    assert client.post("/api/chat", json={"message": "Hi"}, headers=bad).status_code == 401
    forged = jwt.encode({"sub": "sara@example.com"}, "x" * 40, algorithm="HS256")
    headers = {"Authorization": f"Bearer {forged}"}
    assert client.post("/api/chat", json={"message": "Hi"}, headers=headers).status_code == 401


def test_old_email_header_no_longer_works(client):
    headers = {"X-Customer-Email": "sara@example.com"}
    assert client.post("/api/chat", json={"message": "Hi"}, headers=headers).status_code == 401


def test_wrong_password_and_unknown_email_look_the_same(client):
    wrong = client.post("/api/login", json={"email": "sara@example.com", "password": "nope"})
    unknown = client.post("/api/login", json={"email": "nobody@example.com", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_account_locks_after_repeated_failures(client):
    credentials = {"email": "sara@example.com", "password": "nope"}
    for _ in range(MAX_FAILED_LOGINS):
        assert client.post("/api/login", json=credentials).status_code == 401
    correct = {"email": "sara@example.com", "password": PASSWORD}
    assert client.post("/api/login", json=correct).status_code == 401


def test_roles_are_enforced(client, sara, staff, llm):
    assert client.get("/api/staff/escalations", headers=sara).status_code == 403
    assert client.post("/api/chat", json={"message": "Hi"}, headers=staff).status_code == 403


def test_semantic_search_cites_the_matching_passage(client, sara, llm):
    llm.replies += [tool("search_help_docs", query=WARRANTY_TEXT), LLMReply(text="5 years. [[policies.md#Warranty]]")]
    body = client.post("/api/chat", json={"message": "Chair warranty?"}, headers=sara).json()
    assert body["reply"] == "5 years."
    assert body["sources"] == [{"source": "policies.md#Warranty", "text": WARRANTY_TEXT}]
    conversation = client.get("/api/conversations/latest", headers=sara).json()
    assert conversation["messages"][-1]["sources"] == body["sources"]


def test_only_cited_passages_become_sources(client, sara, llm):
    llm.replies += [
        tool("search_help_docs", query="warranty returns NW-4471"),
        LLMReply(text="Answer. [[policies.md#Recalled parts]]"),
    ]
    body = client.post("/api/chat", json={"message": "?"}, headers=sara).json()
    assert [s["source"] for s in body["sources"]] == ["policies.md#Recalled parts"]


def test_invented_citations_are_dropped(client, sara, llm):
    llm.replies += [
        tool("search_help_docs", query=WARRANTY_TEXT),
        LLMReply(text="Answer. [[policies.md#Made up]]"),
    ]
    body = client.post("/api/chat", json={"message": "?"}, headers=sara).json()
    assert (body["reply"], body["sources"]) == ("Answer.", [])


def test_reply_that_is_only_a_citation_hands_off(client, sara, llm):
    llm.replies.append(LLMReply(text="[[policies.md#Warranty]]"))
    assert client.post("/api/chat", json={"message": "?"}, headers=sara).json()["status"] == "escalated"


def stream_events(client, headers, **body):
    response = client.post("/api/chat/stream", json=body, headers=headers)
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines()]


def test_stream_reports_real_steps_then_the_result(client, sara, llm):
    llm.replies += [tool("get_order", order_id="A1001"), LLMReply(text="Shipped.")]
    events = stream_events(client, sara, message="A1001?")
    assert events[0] == {"type": "step", "label": "Checking your order"}
    assert (events[-1]["type"], events[-1]["reply"]) == ("done", "Shipped.")


def test_stream_requires_login_and_ownership(client, sara, omar, llm):
    assert client.post("/api/chat/stream", json={"message": "Hi"}).status_code == 401
    llm.replies.append(LLMReply(text="Hi."))
    conversation_id = stream_events(client, sara, message="Hi")[-1]["conversation_id"]
    response = client.post(
        "/api/chat/stream", json={"message": "Hi", "conversation_id": conversation_id}, headers=omar
    )
    assert response.status_code == 404


def test_stream_reports_failures(client, sara, store, monkeypatch):
    monkeypatch.setattr(store, "get_messages", lambda _: 1 / 0)
    events = stream_events(client, sara, message="Hi")
    assert events == [{"type": "error", "detail": "Something went wrong. Please try again."}]


def bearer(session):
    return {"Authorization": f"Bearer {session['token']}"}


def demo_pair(client):
    body = client.post("/api/demo/pair").json()
    return bearer(body["customer"]), bearer(body["staff"]), body


def test_demo_session_creates_a_private_account(client):
    body = client.post("/api/demo/session", json={"role": "customer"}).json()
    assert body["user"]["demo"] is True and body["user"]["email"] != "sara@example.com"
    assert client.post("/api/demo/session", json={"role": "admin"}).status_code == 422


def test_demo_staff_alone_gets_a_sample_ticket(client):
    headers = bearer(client.post("/api/demo/session", json={"role": "staff"}).json())
    queue = client.get("/api/staff/escalations", headers=headers).json()
    assert len(queue) == 1 and queue[0]["customer_name"] == "Demo Customer"
    detail = client.get(f"/api/staff/conversations/{queue[0]['conversation_id']}", headers=headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["tool_calls"][0]["name"] == "escalate_to_human"


def test_demo_customers_have_their_own_orders(client, store):
    _, _, first = demo_pair(client)
    _, _, second = demo_pair(client)
    first_email, second_email = first["customer"]["user"]["email"], second["customer"]["user"]["email"]
    assert store.get_order("A1001", first_email)["item"] == "Standing desk"
    assert store.get_order("A1001", second_email)["item"] == "Standing desk"
    assert store.get_order("B2001", first_email) is None


def test_demo_workspaces_cannot_see_each_other_or_the_real_queue(client, sara, staff, llm):
    first_customer, first_staff, _ = demo_pair(client)
    _, second_staff, _ = demo_pair(client)
    llm.replies += [
        tool("escalate_to_human", reason="demo", summary="Demo ticket"),
        tool("escalate_to_human", reason="real", summary="Real ticket"),
    ]
    demo_id = client.post("/api/chat", json={"message": "Refund"}, headers=first_customer).json()["conversation_id"]
    real_id = client.post("/api/chat", json={"message": "Refund"}, headers=sara).json()["conversation_id"]

    assert [i["reason"] for i in client.get("/api/staff/escalations", headers=first_staff).json()] == ["demo"]
    assert client.get("/api/staff/escalations", headers=second_staff).json() == []
    real_queue = client.get("/api/staff/escalations", headers=staff).json()
    assert [i["reason"] for i in real_queue] == ["real"]

    # Escalation ids are sequential, so every staff route has to check the workspace itself.
    for outsider, conversation_id in ((second_staff, demo_id), (first_staff, real_id), (staff, demo_id)):
        assert client.get(f"/api/staff/conversations/{conversation_id}", headers=outsider).status_code == 404
        reply = client.post(f"/api/staff/conversations/{conversation_id}/reply", json={"message": "x"}, headers=outsider)
        assert reply.status_code == 404
    assert client.post(f"/api/staff/escalations/{real_queue[0]['id']}/resolve", headers=first_staff).status_code == 404
    assert client.get("/api/staff/escalations", headers=staff).json()[0]["reason"] == "real"


def test_expired_demo_workspaces_are_deleted_and_real_accounts_kept(client, store, database_url, llm):
    customer, _, old = demo_pair(client)
    llm.replies += [tool("escalate_to_human", reason="old", summary="Old")]
    client.post("/api/chat", json={"message": "Refund"}, headers=customer)
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE users SET created_at = now() - interval '2 days' WHERE workspace IS NOT NULL")
    demo_pair(client)
    assert store.get_user(old["customer"]["user"]["email"]) is None
    assert store.get_user("sara@example.com") is not None


def google_client(store, llm, claims):
    def verify(credential, client_id):
        assert client_id == "client-id"
        if claims is None:
            raise ValueError("bad token")
        return claims

    app = create_app(
        store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET,
        google_client_id="client-id", verify_google=verify,
    )
    return TestClient(app)


def test_google_sign_in_creates_a_customer_with_sample_orders(store, llm):
    claims = {"email": "New.Person@Gmail.com", "email_verified": True, "name": "New Person"}
    client = google_client(store, llm, claims)
    body = client.post("/api/auth/google", json={"credential": "token"}).json()
    assert body["user"] == {
        "email": "new.person@gmail.com", "role": "customer", "name": "New Person",
        "has_password": False, "demo": False, "can_switch": True,
    }
    assert store.get_order("A1001", "new.person@gmail.com") is not None
    # Signing in again finds the same account rather than failing or duplicating orders.
    assert client.post("/api/auth/google", json={"credential": "token"}).status_code == 200
    assert len(store.list_orders("new.person@gmail.com")) == 2


def test_google_account_cannot_sign_in_with_a_password(store, llm, client):
    google_client(store, llm, {"email": "g@gmail.com", "email_verified": True}).post(
        "/api/auth/google", json={"credential": "token"}
    )
    assert client.post("/api/login", json={"email": "g@gmail.com", "password": "anything"}).status_code == 401


def test_google_never_grants_staff_access(store, llm):
    client = google_client(store, llm, {"email": "agent@example.com", "email_verified": True})
    assert client.post("/api/auth/google", json={"credential": "token"}).status_code == 403


def test_google_rejects_unverified_and_invalid_tokens(store, llm):
    unverified = google_client(store, llm, {"email": "x@gmail.com", "email_verified": False})
    assert unverified.post("/api/auth/google", json={"credential": "token"}).status_code == 401
    assert store.get_user("x@gmail.com") is None
    invalid = google_client(store, llm, None)
    assert invalid.post("/api/auth/google", json={"credential": "token"}).status_code == 401


def test_google_sign_in_is_off_without_a_client_id(client):
    assert client.post("/api/auth/google", json={"credential": "token"}).status_code == 404


def test_change_password(client, sara):
    wrong = {"current_password": "not-it", "new_password": "a-new-password"}
    assert client.post("/api/me/password", json=wrong, headers=sara).status_code == 400
    short = {"current_password": PASSWORD, "new_password": "short"}
    assert client.post("/api/me/password", json=short, headers=sara).status_code == 422
    good = {"current_password": PASSWORD, "new_password": "a-new-password"}
    assert client.post("/api/me/password", json=good, headers=sara).status_code == 200
    assert client.post("/api/login", json={"email": "sara@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/login", json={"email": "sara@example.com", "password": "a-new-password"}).status_code == 200


def test_google_account_can_set_a_first_password(store, llm, client):
    body = google_client(store, llm, {"email": "g@gmail.com", "email_verified": True}).post(
        "/api/auth/google", json={"credential": "token"}
    ).json()
    assert client.post("/api/me/password", json={"new_password": "a-new-password"}, headers=bearer(body)).status_code == 200
    assert client.get("/api/me", headers=bearer(client.post(
        "/api/login", json={"email": "g@gmail.com", "password": "a-new-password"}
    ).json())).json()["has_password"] is True


def test_demo_account_cannot_set_a_password(client):
    customer, _, _ = demo_pair(client)
    assert client.post("/api/me/password", json={"new_password": "a-new-password"}, headers=customer).status_code == 403


def register(client, email="new@example.com", password="a-new-password"):
    return client.post("/api/register", json={"email": email, "name": "New Person", "password": password})


def test_registering_creates_a_lasting_account_with_its_own_orders(client, store):
    body = register(client, "New@Example.com").json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["demo"] is False and body["user"]["can_switch"] is True
    assert store.get_order("A1001", "new@example.com")["item"] == "Standing desk"
    assert client.post("/api/login", json={"email": "new@example.com", "password": "a-new-password"}).status_code == 200


def test_registering_rejects_bad_input(client):
    assert register(client, password="short").status_code == 422
    assert client.post(
        "/api/register", json={"email": "x@example.com", "name": "   ", "password": "a-new-password"}
    ).status_code == 422
    assert register(client, "not-an-email").status_code == 422


def test_registering_cannot_take_an_existing_or_reserved_email(client):
    assert register(client).status_code == 200
    assert register(client, password="another-password").status_code == 409
    assert client.post("/api/login", json={"email": "new@example.com", "password": "a-new-password"}).status_code == 200
    assert register(client, "agent@example.com").status_code == 409
    for email in ("demo@tendline.app", "support@tendline.app", "support-abc@tendline.app"):
        assert register(client, email).status_code == 400


def test_registered_account_sees_its_handoff_from_the_support_side(client, staff, llm):
    customer = bearer(register(client).json())
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    conversation_id = client.post("/api/chat", json={"message": "Refund"}, headers=customer).json()["conversation_id"]

    switched = client.post("/api/me/switch", headers=customer).json()
    assert switched["user"]["role"] == "staff" and switched["user"]["can_switch"] is True
    agent = bearer(switched)
    assert [i["conversation_id"] for i in client.get("/api/staff/escalations", headers=agent).json()] == [conversation_id]
    assert client.post(
        f"/api/staff/conversations/{conversation_id}/reply", json={"message": "Done"}, headers=agent
    ).status_code == 200
    assert client.get("/api/staff/escalations", headers=staff).json() == []

    back = client.post("/api/me/switch", headers=agent).json()
    assert back["user"]["email"] == "new@example.com"


def test_registered_workspaces_stay_private(client, llm):
    first = bearer(register(client, "one@example.com").json())
    second = bearer(register(client, "two@example.com").json())
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    conversation_id = client.post("/api/chat", json={"message": "Refund"}, headers=first).json()["conversation_id"]
    outsider = bearer(client.post("/api/me/switch", headers=second).json())
    assert client.get("/api/staff/escalations", headers=outsider).json() == []
    assert client.get(f"/api/staff/conversations/{conversation_id}", headers=outsider).status_code == 404


def test_only_registered_accounts_can_switch_view(client, sara, staff):
    assert client.post("/api/me/switch", headers=sara).status_code == 404
    assert client.post("/api/me/switch", headers=staff).status_code == 404
    customer, demo_staff, _ = demo_pair(client)
    assert client.post("/api/me/switch", headers=customer).status_code == 404
    assert client.post("/api/me/switch", headers=demo_staff).status_code == 404
    assert client.post("/api/me/switch").status_code == 401


def test_support_side_of_a_registered_account_has_no_password(client):
    customer = bearer(register(client).json())
    agent = client.post("/api/me/switch", headers=customer).json()
    assert agent["user"]["has_password"] is False
    assert client.post(
        "/api/me/password", json={"new_password": "a-new-password"}, headers=bearer(agent)
    ).status_code == 403
    assert client.post(
        "/api/me/password", json={"current_password": "a-new-password", "new_password": "newer-password"}, headers=customer
    ).status_code == 200


def test_registered_accounts_survive_demo_cleanup(client, store, database_url):
    register(client)
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE users SET created_at = now() - interval '30 days' WHERE permanent")
    demo_pair(client)
    assert client.post("/api/login", json={"email": "new@example.com", "password": "a-new-password"}).status_code == 200
    with psycopg.connect(database_url, autocommit=True) as db:
        assert db.execute("SELECT count(*) FROM users WHERE permanent").fetchone()[0] == 2


def test_google_sign_in_removes_a_password_set_before_the_email_was_proven(store, llm, client):
    assert register(client, "victim@gmail.com", "attacker-password").status_code == 200
    body = google_client(store, llm, {"email": "victim@gmail.com", "email_verified": True}).post(
        "/api/auth/google", json={"credential": "token"}
    ).json()
    assert body["user"]["has_password"] is False
    assert client.post("/api/login", json={"email": "victim@gmail.com", "password": "attacker-password"}).status_code == 401


def test_google_sign_in_keeps_a_password_set_afterwards(store, llm, client):
    google = google_client(store, llm, {"email": "g@gmail.com", "email_verified": True})
    headers = bearer(google.post("/api/auth/google", json={"credential": "token"}).json())
    assert client.post("/api/me/password", json={"new_password": "a-new-password"}, headers=headers).status_code == 200
    assert google.post("/api/auth/google", json={"credential": "token"}).json()["user"]["has_password"] is True


def test_google_accounts_get_a_private_workspace(store, llm, staff, client):
    google = google_client(store, llm, {"email": "g@gmail.com", "email_verified": True})
    body = google.post("/api/auth/google", json={"credential": "token"}).json()
    assert body["user"]["can_switch"] is True
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    client.post("/api/chat", json={"message": "Refund"}, headers=bearer(body))
    assert client.get("/api/staff/escalations", headers=staff).json() == []


def test_registering_is_rate_limited(store, llm):
    client = limited_client(store, llm, register=1)
    assert register(client, "one@example.com").status_code == 200
    blocked = register(client, "two@example.com")
    assert blocked.status_code == 429 and blocked.json()["detail"] == "register limit"


@pytest.mark.parametrize("message", ["   ", "a\x00b", "\ud800", "x" * 2001])
def test_unusable_messages_are_rejected_before_anything_is_saved(client, sara, staff, store, message):
    payload = json.dumps({"message": message})
    headers = {**sara, "Content-Type": "application/json"}
    assert client.post("/api/chat", content=payload, headers=headers).status_code == 422
    assert store.latest_conversation("sara@example.com") is None


@pytest.mark.parametrize("name", ["a\x00b", "\ud800"])
def test_unusable_names_are_rejected(client, name):
    payload = json.dumps({"email": "new@example.com", "name": name, "password": "a-new-password"})
    response = client.post("/api/register", content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_unusable_conversation_id_is_rejected(client, sara):
    payload = json.dumps({"message": "Hi", "conversation_id": "a\x00b"})
    headers = {**sara, "Content-Type": "application/json"}
    assert client.post("/api/chat", content=payload, headers=headers).status_code == 422


def test_rejected_requests_do_not_repeat_the_input(client):
    response = client.post("/api/login", json={"email": "x@example.com", "password": "secret-" + "x" * 300})
    assert response.status_code == 422 and "secret-" not in response.text


def test_messages_are_saved_without_surrounding_space(client, sara, llm):
    llm.replies += [LLMReply(text="Hello.")]
    conversation_id = client.post("/api/chat", json={"message": "  Hi  "}, headers=sara).json()["conversation_id"]
    assert client.get(f"/api/conversations/{conversation_id}", headers=sara).json()["messages"][0]["text"] == "Hi"


def test_new_message_during_a_handoff_stays_in_that_conversation(client, sara, staff, llm):
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    first = client.post("/api/chat", json={"message": "Refund"}, headers=sara).json()["conversation_id"]
    second = client.post("/api/chat", json={"message": "Any news?"}, headers=sara).json()
    assert second["conversation_id"] == first and second["status"] == "escalated"
    assert len(client.get("/api/staff/escalations", headers=staff).json()) == 1


def test_latest_conversation_prefers_an_open_handoff(client, store, sara, llm):
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    handed_off = client.post("/api/chat", json={"message": "Refund"}, headers=sara).json()["conversation_id"]
    newer = store.create_conversation("sara@example.com")
    assert newer != handed_off
    assert client.get("/api/conversations/latest", headers=sara).json()["id"] == handed_off


def test_daily_ai_limit_does_not_stop_messages_to_the_team(store, llm):
    client = limited_client(store, llm, chat_all=1)
    sara = login(client, "sara@example.com")
    llm.replies += [tool("escalate_to_human", reason="refund", summary="Refund")]
    conversation_id = client.post("/api/chat", json={"message": "Refund"}, headers=sara).json()["conversation_id"]
    body = {"message": "Any news?", "conversation_id": conversation_id}
    assert client.post("/api/chat", json=body, headers=sara).status_code == 200
    omar = login(client, "omar@example.com")
    blocked = client.post("/api/chat", json={"message": "Hi"}, headers=omar)
    assert blocked.status_code == 429 and blocked.json()["detail"] == "chat_all limit"


class FakeMail:
    def __init__(self):
        self.sent = []

    def __call__(self, to, subject, heading, text, action_label, action_url, note_lead, note):
        self.sent.append(SimpleNamespace(to=to, url=action_url))

    def token(self):
        return self.sent[-1].url.split("token=")[1]


@pytest.fixture
def mail():
    return FakeMail()


@pytest.fixture
def reset_client(store, llm, mail):
    return TestClient(create_app(
        store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, demo_mode=True,
        send_email=mail, app_url="https://app.example",
    ))


def forgot(client, email="sara@example.com"):
    return client.post("/api/password/forgot", json={"email": email})


def test_reset_email_has_plain_and_styled_parts_and_escapes_text(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    send = mailer.smtp_sender(
        "smtp.example", 587, "user", "pass", "user@example.com", "https://app.example/email-logo.png"
    )
    url = "https://app.example/reset-password?token=a&b"
    send("sara@example.com", "Subject", "Heading", "For <b>you</b>", "Go", url, "Soon.", "Ignore it.")

    plain = sent[0].get_body(preferencelist=("plain",)).get_content()
    html = sent[0].get_body(preferencelist=("html",)).get_content()
    assert sent[0]["From"] == "Tendline <user@example.com>"
    assert mailer.named_sender("Support <user@example.com>") == "Support <user@example.com>"
    assert 'src="https://app.example/email-logo.png"' in html
    assert f"Go: {url}" in plain and "Soon. Ignore it." in plain
    assert "For &lt;b&gt;you&lt;/b&gt;" in html and "<b>you" not in html
    assert 'href="https://app.example/reset-password?token=a&amp;b"' in html


def test_reset_replies_the_same_for_known_and_unknown_emails(reset_client, mail):
    known, unknown = forgot(reset_client), forgot(reset_client, "nobody@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert [m.to for m in mail.sent] == ["sara@example.com"]
    assert mail.sent[0].url.startswith("https://app.example/reset-password?token=")


def test_reset_link_ignores_forged_host_headers(reset_client, mail):
    reset_client.post("/api/password/forgot", json={"email": "sara@example.com"}, headers={"Host": "evil.example", "Origin": "https://evil.example"})
    assert "evil.example" not in mail.sent[0].url


def test_reset_changes_the_password_and_works_once(reset_client, mail):
    forgot(reset_client)
    body = {"token": mail.token(), "new_password": "a-brand-new-password"}
    assert reset_client.post("/api/password/reset", json=body).status_code == 200
    assert reset_client.post("/api/login", json={"email": "sara@example.com", "password": "a-brand-new-password"}).status_code == 200
    assert reset_client.post("/api/login", json={"email": "sara@example.com", "password": PASSWORD}).status_code == 401
    again = reset_client.post("/api/password/reset", json={**body, "new_password": "another-password"})
    assert again.status_code == 400


def test_reset_token_is_not_stored_in_the_clear(reset_client, mail, database_url):
    forgot(reset_client)
    with psycopg.connect(database_url) as db:
        stored = db.execute("SELECT token_hash FROM password_reset_tokens").fetchone()[0]
    assert stored != mail.token() and mail.token() not in stored


def test_expired_and_invalid_reset_tokens_are_refused(reset_client, mail, database_url):
    forgot(reset_client)
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE password_reset_tokens SET expires_at = now() - interval '1 minute'")
    body = {"token": mail.token(), "new_password": "a-brand-new-password"}
    assert reset_client.post("/api/password/reset", json=body).status_code == 400
    assert reset_client.post("/api/password/reset", json={**body, "token": "made-up"}).status_code == 400
    assert reset_client.post("/api/login", json={"email": "sara@example.com", "password": PASSWORD}).status_code == 200


def test_a_new_reset_link_replaces_the_old_one(reset_client, mail):
    forgot(reset_client)
    old = mail.token()
    forgot(reset_client)
    assert reset_client.post("/api/password/reset", json={"token": old, "new_password": "a-brand-new-password"}).status_code == 400
    assert reset_client.post("/api/password/reset", json={"token": mail.token(), "new_password": "a-brand-new-password"}).status_code == 200


def test_reset_ends_older_sessions_including_the_support_view(reset_client, mail):
    customer = bearer(register(reset_client).json())
    agent = bearer(reset_client.post("/api/me/switch", headers=customer).json())
    time.sleep(1.1)
    forgot(reset_client, "new@example.com")
    assert reset_client.post("/api/password/reset", json={"token": mail.token(), "new_password": "a-brand-new-password"}).status_code == 200
    assert reset_client.get("/api/me", headers=customer).status_code == 401
    assert reset_client.get("/api/me", headers=agent).status_code == 401
    fresh = reset_client.post("/api/login", json={"email": "new@example.com", "password": "a-brand-new-password"}).json()
    assert reset_client.get("/api/me", headers=bearer(fresh)).status_code == 200


def test_changing_a_password_keeps_this_session_and_ends_the_others(client, sara):
    other = login(client, "sara@example.com")
    time.sleep(1.1)
    renewed = client.post("/api/me/password", json={"current_password": PASSWORD, "new_password": "a-brand-new-password"}, headers=sara)
    assert renewed.status_code == 200
    assert client.get("/api/me", headers=bearer(renewed.json())).status_code == 200
    assert client.get("/api/me", headers=other).status_code == 401


def test_reset_clears_an_account_lock(reset_client, mail):
    for _ in range(MAX_FAILED_LOGINS):
        reset_client.post("/api/login", json={"email": "sara@example.com", "password": "wrong-password"})
    forgot(reset_client)
    reset_client.post("/api/password/reset", json={"token": mail.token(), "new_password": "a-brand-new-password"})
    assert reset_client.post("/api/login", json={"email": "sara@example.com", "password": "a-brand-new-password"}).status_code == 200


def test_reset_proves_ownership_so_google_keeps_the_new_password(reset_client, store, llm, mail):
    register(reset_client, "owner@gmail.com")
    forgot(reset_client, "owner@gmail.com")
    reset_client.post("/api/password/reset", json={"token": mail.token(), "new_password": "a-brand-new-password"})
    body = google_client(store, llm, {"email": "owner@gmail.com", "email_verified": True}).post("/api/auth/google", json={"credential": "token"}).json()
    assert body["user"]["has_password"] is True


def test_google_sign_in_ends_a_strangers_session(store, llm, client):
    stranger = bearer(register(client, "victim@gmail.com", "attacker-password").json())
    time.sleep(1.1)
    google_client(store, llm, {"email": "victim@gmail.com", "email_verified": True}).post("/api/auth/google", json={"credential": "token"})
    assert client.get("/api/me", headers=stranger).status_code == 401


def test_demo_and_support_view_accounts_get_no_reset_link(reset_client, mail):
    customer, staff_side, _ = demo_pair(reset_client)
    forgot(reset_client, reset_client.get("/api/me", headers=customer).json()["email"])
    forgot(reset_client, reset_client.get("/api/me", headers=staff_side).json()["email"])
    owner = bearer(register(reset_client).json())
    support = reset_client.post("/api/me/switch", headers=owner).json()["user"]["email"]
    forgot(reset_client, support)
    assert mail.sent == []


def test_a_mail_failure_gives_the_same_reply(store, llm):
    def broken(*args):
        raise OSError("smtp down")

    client = TestClient(create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, send_email=broken))
    response = forgot(client)
    assert response.status_code == 200 and response.json()["detail"] == forgot(client, "nobody@example.com").json()["detail"]


def test_reset_is_off_without_a_mail_service(client):
    assert forgot(client).status_code == 503


def test_reset_emails_are_limited_per_address_without_revealing_it(store, llm, mail):
    limits = {**RATE_LIMITS, "reset_email": (1, timedelta(hours=1), "x")}
    client = TestClient(create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, send_email=mail, rate_limits=limits))
    assert forgot(client).status_code == 200
    assert forgot(client).status_code == 200
    assert len(mail.sent) == 1


def limited_client(store, llm, behind_proxy=False, **rules):
    limits = {**RATE_LIMITS, **{name: (hits, timedelta(hours=1), f"{name} limit") for name, hits in rules.items()}}
    app = create_app(
        store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, demo_mode=True,
        behind_proxy=behind_proxy, rate_limits=limits,
    )
    return TestClient(app)


def test_chat_is_limited_per_user_before_calling_the_model(store, llm):
    client = limited_client(store, llm, chat_user=1)
    sara = login(client, "sara@example.com")
    llm.replies += [LLMReply(text="Hello.")]
    assert client.post("/api/chat", json={"message": "Hi"}, headers=sara).status_code == 200
    blocked = client.post("/api/chat", json={"message": "Hi"}, headers=sara)
    assert blocked.status_code == 429 and blocked.json()["detail"] == "chat_user limit"
    assert int(blocked.headers["Retry-After"]) > 0
    assert client.post("/api/chat/stream", json={"message": "Hi"}, headers=sara).status_code == 429
    assert len(llm.histories) == 1
    # One customer running out doesn't affect another.
    llm.replies += [LLMReply(text="Hello.")]
    assert client.post("/api/chat", json={"message": "Hi"}, headers=login(client, "omar@example.com")).status_code == 200


def test_daily_chat_cap_covers_everyone(store, llm):
    client = limited_client(store, llm, chat_all=1)
    llm.replies += [LLMReply(text="Hello.")]
    assert client.post("/api/chat", json={"message": "Hi"}, headers=login(client, "sara@example.com")).status_code == 200
    assert client.post("/api/chat", json={"message": "Hi"}, headers=login(client, "omar@example.com")).status_code == 429


def test_login_and_demo_are_limited_per_address(store, llm):
    client = limited_client(store, llm, login=1, demo=1)
    bad = {"email": "sara@example.com", "password": "wrong"}
    assert client.post("/api/login", json=bad).status_code == 401
    assert client.post("/api/login", json=bad).status_code == 429
    assert client.post("/api/demo/pair").status_code == 200
    assert client.post("/api/demo/pair").status_code == 429


def test_rate_limit_window_resets(store):
    assert store.hit_rate_limit("key", 1, timedelta(hours=1)) is None
    assert store.hit_rate_limit("key", 1, timedelta(hours=1)) is not None
    assert store.hit_rate_limit("other", 1, timedelta(hours=1)) is None
    assert store.hit_rate_limit("short", 1, timedelta(milliseconds=1)) is None
    time.sleep(0.05)
    assert store.hit_rate_limit("short", 1, timedelta(milliseconds=1)) is None


def test_forwarded_address_is_only_trusted_behind_a_proxy(store, llm):
    direct = limited_client(store, llm, demo=1)
    assert direct.post("/api/demo/pair", headers={"x-real-ip": "1.1.1.1"}).status_code == 200
    assert direct.post("/api/demo/pair", headers={"x-real-ip": "2.2.2.2"}).status_code == 429
    proxied = limited_client(store, llm, behind_proxy=True, demo=1)
    assert proxied.post("/api/demo/pair", headers={"x-real-ip": "3.3.3.3"}).status_code == 200
    assert proxied.post("/api/demo/pair", headers={"x-real-ip": "4.4.4.4"}).status_code == 200
    assert proxied.post("/api/demo/pair", headers={"x-real-ip": "4.4.4.4"}).status_code == 429


def test_public_demo_logins_open_private_workspaces(client, staff, llm):
    credentials = {"email": "demo@tendline.app", "password": "demo1234"}
    first = client.post("/api/login", json=credentials).json()
    second = client.post("/api/login", json=credentials).json()
    assert first["user"]["role"] == "customer" and first["user"]["demo"] is True
    assert first["user"]["email"] != second["user"]["email"]
    llm.replies += [tool("escalate_to_human", reason="demo", summary="Demo ticket")]
    client.post("/api/chat", json={"message": "Refund"}, headers=bearer(first))
    assert client.get("/api/staff/escalations", headers=staff).json() == []

    agent = client.post("/api/login", json={"email": "support@tendline.app", "password": "demo1234"}).json()
    assert agent["user"]["role"] == "staff" and agent["user"]["demo"] is True
    queue = client.get("/api/staff/escalations", headers=bearer(agent)).json()
    assert [item["reason"] for item in queue] != ["demo"] and len(queue) == 1


def test_public_demo_logins_need_the_right_password_and_demo_mode(client, store, llm):
    wrong = {"email": "support@tendline.app", "password": "wrong"}
    assert client.post("/api/login", json=wrong).status_code == 401
    app = create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET)
    right = {"email": "support@tendline.app", "password": "demo1234"}
    assert TestClient(app).post("/api/login", json=right).status_code == 401


def test_cors_allows_only_the_configured_frontend(store, llm):
    app = create_app(
        store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET, cors_origins=["https://app.example.com"]
    )
    client = TestClient(app)
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    allowed = client.options("/api/chat/stream", headers={"Origin": "https://app.example.com", **preflight})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "access-control-allow-credentials" not in allowed.headers
    other = client.options("/api/chat/stream", headers={"Origin": "https://evil.example.com", **preflight})
    assert "access-control-allow-origin" not in other.headers


def test_demo_session_is_off_by_default(store, llm):
    app = create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET)
    assert TestClient(app).post("/api/demo/session", json={"role": "staff"}).status_code == 404


def test_queue_is_oldest_first_and_flags_new_customer_messages(client, sara, omar, staff, llm):
    llm.replies += [
        tool("escalate_to_human", reason="first", summary="First"),
        tool("escalate_to_human", reason="second", summary="Second"),
    ]
    first = client.post("/api/chat", json={"message": "One"}, headers=sara).json()["conversation_id"]
    client.post("/api/chat", json={"message": "Two"}, headers=omar)
    client.post("/api/chat", json={"message": "Still there?", "conversation_id": first}, headers=sara)
    queue = client.get("/api/staff/escalations", headers=staff).json()
    assert [item["reason"] for item in queue] == ["first", "second"]
    assert [item["last_role"] for item in queue] == ["user", "assistant"]


def test_search_returns_nothing_for_unrelated_text(client, sara, llm):
    llm.replies += [tool("search_help_docs", query="zebra quantum volcano"), LLMReply(text="Not sure.")]
    client.post("/api/chat", json={"message": "?"}, headers=sara)
    assert llm.histories[1][-1]["results"][0][1] == {"passages": []}


def test_ingest_only_embeds_changed_passages(store):
    HashEmbedder.calls = 0
    assert ingest(store, HashEmbedder(), BASE_DIR / "data" / "docs") == 0
    assert HashEmbedder.calls == 0


def test_order_lookup_is_scoped_to_the_customer(client, sara, llm):
    llm.replies += [tool("get_order", order_id="B2001"), LLMReply(text="Not found.")]
    client.post("/api/chat", json={"message": "Where is B2001?"}, headers=sara)
    assert llm.histories[1][-1]["results"][0] == ("get_order", {"error": "order not found"})


def test_model_supplied_email_is_ignored(client, sara, llm):
    llm.replies += [tool("list_my_orders", customer_email="omar@example.com"), LLMReply(text="Done.")]
    client.post("/api/chat", json={"message": "List orders"}, headers=sara)
    orders = llm.histories[1][-1]["results"][0][1]["orders"]
    assert {o["id"] for o in orders} == {"A1001", "A1002"}


def test_customer_cannot_open_another_conversation(client, sara, omar, llm):
    llm.replies.append(LLMReply(text="Hi."))
    conversation_id = client.post("/api/chat", json={"message": "Hi"}, headers=sara).json()["conversation_id"]
    assert client.get(f"/api/conversations/{conversation_id}", headers=omar).status_code == 404
    response = client.post(
        "/api/chat", json={"message": "Hi", "conversation_id": conversation_id}, headers=omar
    )
    assert response.status_code == 404
    assert client.get("/api/conversations/latest", headers=omar).json() is None


def test_full_handoff_from_escalation_to_resolution(client, sara, staff, llm):
    llm.replies.append(tool("escalate_to_human", reason="refund", summary="Wants a refund on A1001"))
    body = client.post("/api/chat", json={"message": "Refund please"}, headers=sara).json()
    assert (body["status"], body["reply"]) == ("escalated", HANDOFF_REPLY)
    conversation_id = body["conversation_id"]

    follow_up = client.post(
        "/api/chat", json={"message": "Hello?", "conversation_id": conversation_id}, headers=sara
    ).json()
    assert (follow_up["status"], follow_up["reply"]) == ("escalated", "")
    assert len(llm.histories) == 1

    queue = client.get("/api/staff/escalations", headers=staff).json()
    assert queue[0]["summary"] == "Wants a refund on A1001"
    assert queue[0]["customer_email"] == "sara@example.com"

    detail = client.get(f"/api/staff/conversations/{conversation_id}", headers=staff).json()
    assert detail["tool_calls"][0]["name"] == "escalate_to_human"
    assert [m["text"] for m in detail["messages"]][-1] == "Hello?"

    client.post(
        f"/api/staff/conversations/{conversation_id}/reply",
        json={"message": "Refund approved."},
        headers=staff,
    )
    messages = client.get(f"/api/conversations/{conversation_id}", headers=sara).json()["messages"]
    assert messages[-1] == {"role": "staff", "text": "Refund approved.", "sources": []}

    assert client.post(f"/api/staff/escalations/{queue[0]['id']}/resolve", headers=staff).status_code == 200
    assert client.get("/api/staff/escalations", headers=staff).json() == []
    assert client.post(f"/api/staff/escalations/{queue[0]['id']}/resolve", headers=staff).status_code == 404
    messages = client.get(f"/api/conversations/{conversation_id}", headers=sara).json()["messages"]
    assert messages[-1]["role"] == "event"

    llm.replies.append(LLMReply(text="Anything else?"))
    resumed = client.post(
        "/api/chat", json={"message": "Thanks", "conversation_id": conversation_id}, headers=sara
    ).json()
    assert (resumed["status"], resumed["reply"]) == ("open", "Anything else?")
    assert not any(entry["role"] == "event" for entry in llm.histories[-1])
    assert any(entry["role"] == "staff" for entry in llm.histories[-1])


def test_customer_cannot_use_staff_conversation_routes(client, sara):
    assert client.get("/api/staff/conversations/x", headers=sara).status_code == 403
    response = client.post("/api/staff/conversations/x/reply", json={"message": "Hi"}, headers=sara)
    assert response.status_code == 403


def test_model_failure_hands_off(client, sara, staff, llm):
    llm.replies.append(RuntimeError("provider down"))
    body = client.post("/api/chat", json={"message": "Hi"}, headers=sara).json()
    assert body["status"] == "escalated"
    assert client.get("/api/staff/escalations", headers=staff).json()[0]["reason"] == "model_error"


def test_search_failure_hands_off(store, llm, staff):
    class BrokenEmbedder(HashEmbedder):
        def embed_query(self, text):
            raise RuntimeError("embedding down")

    client = TestClient(create_app(store=store, llm=llm, embedder=BrokenEmbedder(), jwt_secret=SECRET))
    llm.replies.append(tool("search_help_docs", query="warranty"))
    headers = login(client, "sara@example.com")
    assert client.post("/api/chat", json={"message": "Hi"}, headers=headers).json()["status"] == "escalated"
    assert client.get("/api/staff/escalations", headers=staff).json()[0]["reason"] == "tool_error"


def test_empty_model_reply_hands_off(client, sara, llm):
    llm.replies.append(LLMReply(text="  "))
    assert client.post("/api/chat", json={"message": "Hi"}, headers=sara).json()["status"] == "escalated"


def test_step_limit_hands_off(client, sara, llm):
    llm.replies += [tool("list_my_orders") for _ in range(MAX_STEPS)]
    assert client.post("/api/chat", json={"message": "Hi"}, headers=sara).json()["status"] == "escalated"
    assert len(llm.histories) == MAX_STEPS


def test_tool_calls_are_audited(client, sara, staff, llm):
    llm.replies += [tool("get_order", order_id="A1001"), LLMReply(text="Shipped.")]
    conversation_id = client.post("/api/chat", json={"message": "A1001?"}, headers=sara).json()["conversation_id"]
    calls = client.get(f"/api/staff/conversations/{conversation_id}", headers=staff).json()["tool_calls"]
    assert calls[0]["name"] == "get_order"
    assert calls[0]["result"]["order"]["tracking_number"] == "TRK-55821"
