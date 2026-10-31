import hashlib
import json
import math
import re
from types import SimpleNamespace

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.agent import HANDOFF_REPLY, MAX_STEPS
from app.config import BASE_DIR
from app.docs_index import DocsIndex, ingest
from app.llm import GeminiClient, LLMReply, ToolCall
from app.main import create_app
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
            "TRUNCATE users, orders, conversations, messages, tool_calls, escalations, doc_passages CASCADE"
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


def test_demo_session_signs_in_only_demo_accounts(client):
    body = client.post("/api/demo/session", json={"role": "staff"}).json()
    assert body["user"]["email"] == "agent@example.com"
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/staff/escalations", headers=headers).status_code == 200
    assert client.post("/api/demo/session", json={"role": "admin"}).status_code == 422


def test_demo_session_is_off_by_default(store, llm):
    app = create_app(store=store, llm=llm, embedder=HashEmbedder(), jwt_secret=SECRET)
    assert TestClient(app).post("/api/demo/session", json={"role": "staff"}).status_code == 404


def test_queue_is_oldest_first_and_flags_new_customer_messages(client, sara, staff, llm):
    llm.replies += [
        tool("escalate_to_human", reason="first", summary="First"),
        tool("escalate_to_human", reason="second", summary="Second"),
    ]
    first = client.post("/api/chat", json={"message": "One"}, headers=sara).json()["conversation_id"]
    client.post("/api/chat", json={"message": "Two"}, headers=sara)
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
