import json
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

from .agent import SupportAgent
from .config import load_settings
from .docs_index import DocsIndex
from .llm import GeminiClient, GeminiEmbedder
from .store import Store

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(hours=8)
DEMO_ACCOUNTS = {"customer": "sara@example.com", "staff": "agent@example.com"}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class DemoSessionRequest(BaseModel):
    role: Literal["customer", "staff"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class StaffReply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def create_app(store=None, llm=None, embedder=None, jwt_secret=None, demo_mode=False):
    if store is None:
        settings = load_settings()
        store = Store(settings["database_url"])
        llm = GeminiClient(settings["gemini_api_key"], settings["chat_models"])
        embedder = GeminiEmbedder(settings["gemini_api_key"])
        jwt_secret = settings["jwt_secret"]
        demo_mode = settings["demo_mode"]

    agent = SupportAgent(llm, store, DocsIndex(store, embedder))
    app = FastAPI(title="Deskhand", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def session_for(user):
        token = jwt.encode(
            {"sub": user["email"], "exp": datetime.now(timezone.utc) + TOKEN_LIFETIME},
            jwt_secret,
            algorithm="HS256",
        )
        return {"token": token, "user": user}

    def current_user(authorization: str = Header(default="")):
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Not signed in")
        try:
            claims = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Not signed in")
        # Read from the database so a removed account or changed role takes effect at once.
        user = store.get_user(claims["sub"])
        if user is None:
            raise HTTPException(status_code=401, detail="Not signed in")
        return user

    def current_customer(user=Depends(current_user)):
        if user["role"] != "customer":
            raise HTTPException(status_code=403, detail="Customers only")
        return user

    def current_staff(user=Depends(current_user)):
        if user["role"] != "staff":
            raise HTTPException(status_code=403, detail="Staff only")
        return user

    def owned_conversation(conversation_id, email):
        conversation = store.get_conversation(conversation_id, email)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    def conversation_or_404(conversation_id):
        conversation = store.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    def conversation_view(conversation):
        return {
            "id": conversation["id"],
            "status": conversation["status"],
            "messages": store.get_messages(conversation["id"]),
        }

    def conversation_for_chat(body, email):
        conversation_id = body.conversation_id or store.create_conversation(email)
        return owned_conversation(conversation_id, email)

    def turn_view(conversation, result):
        return {
            "conversation_id": conversation["id"],
            "reply": result.reply,
            "status": result.status,
            "sources": result.sources,
        }

    @app.post("/api/login")
    def login(body: LoginRequest):
        user = store.authenticate(body.email.lower(), body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Wrong email or password")
        return session_for(user)

    @app.post("/api/demo/session")
    def demo_session(body: DemoSessionRequest):
        # Off unless DEMO_MODE is set, because it signs in without a password.
        if not demo_mode:
            raise HTTPException(status_code=404, detail="Not found")
        return session_for(store.get_user(DEMO_ACCOUNTS[body.role]))

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return user

    @app.post("/api/chat")
    def chat(body: ChatRequest, user=Depends(current_customer)):
        conversation = conversation_for_chat(body, user["email"])
        return turn_view(conversation, agent.handle_message(conversation, body.message))

    @app.post("/api/chat/stream")
    def chat_stream(body: ChatRequest, user=Depends(current_customer)):
        """Same as /api/chat, with one JSON line per real agent step before the final result."""
        conversation = conversation_for_chat(body, user["email"])
        events = queue.Queue()

        def run():
            try:
                result = agent.handle_message(
                    conversation,
                    body.message,
                    on_step=lambda label: events.put({"type": "step", "label": label}),
                )
                events.put({"type": "done", **turn_view(conversation, result)})
            except Exception:
                logger.exception("Chat failed for conversation %s", conversation["id"])
                events.put({"type": "error", "detail": "Something went wrong. Please try again."})

        def stream():
            threading.Thread(target=run, daemon=True).start()
            while True:
                event = events.get()
                yield json.dumps(event) + "\n"
                if event["type"] != "step":
                    return

        return StreamingResponse(
            stream(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/conversations/latest")
    def latest_conversation(user=Depends(current_customer)):
        conversation = store.latest_conversation(user["email"])
        return conversation_view(conversation) if conversation else None

    @app.get("/api/conversations/{conversation_id}")
    def conversation_detail(conversation_id: str, user=Depends(current_customer)):
        return conversation_view(owned_conversation(conversation_id, user["email"]))

    @app.get("/api/staff/escalations", dependencies=[Depends(current_staff)])
    def escalations():
        return store.list_escalations()

    @app.get("/api/staff/conversations/{conversation_id}", dependencies=[Depends(current_staff)])
    def staff_conversation(conversation_id: str):
        conversation = conversation_or_404(conversation_id)
        return {
            **conversation_view(conversation),
            "customer_email": conversation["customer_email"],
            "tool_calls": store.get_tool_calls(conversation_id),
        }

    @app.post("/api/staff/conversations/{conversation_id}/reply", dependencies=[Depends(current_staff)])
    def staff_reply(conversation_id: str, body: StaffReply):
        conversation_or_404(conversation_id)
        store.add_message(conversation_id, "staff", body.message)
        return {"ok": True}

    @app.post("/api/staff/escalations/{escalation_id}/resolve", dependencies=[Depends(current_staff)])
    def resolve(escalation_id: int):
        if not store.resolve_escalation(escalation_id):
            raise HTTPException(status_code=404, detail="Open escalation not found")
        return {"ok": True}

    return app
