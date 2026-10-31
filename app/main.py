import json
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

from .agent import HANDOFF_REPLY, SupportAgent
from .config import load_settings
from .docs_index import DocsIndex
from .llm import GeminiClient, GeminiEmbedder
from .store import Store

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(hours=8)
# Shown on the sign-in page. They open a private workspace, never a shared account.
DEMO_LOGINS = {"demo@deskhand.app": "customer", "support@deskhand.app": "staff"}
DEMO_LOGIN_PASSWORD = "demo1234"
SAMPLE_REQUEST = "I want a refund for order A1001."
SAMPLE_SUMMARY = "Customer requested a refund for order A1001."
BUSY = "Too many requests. Please try again in a little while."
# Name: (allowed hits, window, message). Kept in Postgres because serverless instances share no memory.
RATE_LIMITS = {
    "login": (20, timedelta(minutes=15), "Too many sign-in attempts. Please try again in a few minutes."),
    "demo": (20, timedelta(hours=1), BUSY),
    "demo_all": (1000, timedelta(days=1), "The demo is full for today. Please try again tomorrow."),
    "chat_user": (30, timedelta(hours=1), "You've sent a lot of messages. Please try again in a little while."),
    "chat_ip": (60, timedelta(hours=1), BUSY),
    "chat_all": (300, timedelta(days=1), "The AI assistant has reached its daily limit. Please try again tomorrow."),
    "staff_reply": (60, timedelta(hours=1), BUSY),
}


def verify_google_token(credential, client_id):
    """Return the claims of a Google ID token, checking its signature, audience and expiry."""
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport import requests
    from google.oauth2 import id_token

    try:
        return id_token.verify_oauth2_token(credential, requests.Request(), client_id)
    except (ValueError, GoogleAuthError) as error:
        raise ValueError("Invalid Google token") from error


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class GoogleRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=8000)


class PasswordChange(BaseModel):
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class DemoSessionRequest(BaseModel):
    role: Literal["customer", "staff"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class StaffReply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def public_user(user):
    return {
        "email": user["email"],
        "role": user["role"],
        "name": user["name"],
        "has_password": user["has_password"],
        "demo": user["workspace"] is not None,
    }


def create_app(
    store=None,
    llm=None,
    embedder=None,
    jwt_secret=None,
    demo_mode=False,
    google_client_id="",
    verify_google=verify_google_token,
    behind_proxy=False,
    rate_limits=RATE_LIMITS,
):
    if store is None:
        settings = load_settings()
        store = Store(settings["database_url"])
        llm = GeminiClient(settings["gemini_api_key"], settings["chat_models"])
        embedder = GeminiEmbedder(settings["gemini_api_key"])
        jwt_secret = settings["jwt_secret"]
        demo_mode = settings["demo_mode"]
        google_client_id = settings["google_client_id"]
        behind_proxy = settings["behind_proxy"]

    agent = SupportAgent(llm, store, DocsIndex(store, embedder))
    app = FastAPI(title="Deskhand", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def session_for(user):
        token = jwt.encode(
            {"sub": user["email"], "exp": datetime.now(timezone.utc) + TOKEN_LIFETIME},
            jwt_secret,
            algorithm="HS256",
        )
        return {"token": token, "user": public_user(user)}

    def client_ip(request):
        if behind_proxy and request.headers.get("x-real-ip"):
            return request.headers["x-real-ip"]
        return request.client.host if request.client else "unknown"

    def enforce_limit(name, subject):
        limit, window, message = rate_limits[name]
        wait = store.hit_rate_limit(f"{name}:{subject}", limit, window)
        if wait is not None:
            raise HTTPException(status_code=429, detail=message, headers={"Retry-After": str(wait)})

    def enforce_chat_limits(request, user):
        enforce_limit("chat_user", user["email"])
        enforce_limit("chat_ip", client_ip(request))
        enforce_limit("chat_all", "all")

    def new_demo_workspace(request):
        enforce_limit("demo", client_ip(request))
        enforce_limit("demo_all", "all")
        return store.create_demo_workspace()

    def new_demo_session(role, request):
        customer, staff = new_demo_workspace(request)
        if role == "customer":
            return session_for(customer)
        # A support agent trying the demo alone has nobody to create a ticket for them.
        add_sample_ticket(customer["email"])
        return session_for(staff)

    def add_sample_ticket(customer_email):
        conversation_id = store.create_conversation(customer_email)
        store.add_message(conversation_id, "user", SAMPLE_REQUEST)
        store.log_tool_call(
            conversation_id, "escalate_to_human", {"reason": "refund_request", "summary": SAMPLE_SUMMARY}, {}
        )
        store.escalate(conversation_id, SAMPLE_SUMMARY, SAMPLE_SUMMARY, HANDOFF_REPLY)

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

    def staff_conversation_or_404(conversation_id, staff):
        conversation = store.get_staff_conversation(conversation_id, staff["workspace"])
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
    def login(body: LoginRequest, request: Request):
        enforce_limit("login", client_ip(request))
        role = DEMO_LOGINS.get(body.email.lower())
        if demo_mode and role and body.password == DEMO_LOGIN_PASSWORD:
            return new_demo_session(role, request)
        user = store.authenticate(body.email.lower(), body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Wrong email or password")
        return session_for(user)

    @app.post("/api/demo/session")
    def demo_session(body: DemoSessionRequest, request: Request):
        # Off unless DEMO_MODE is set, because it signs in without a password.
        if not demo_mode:
            raise HTTPException(status_code=404, detail="Not found")
        return new_demo_session(body.role, request)

    @app.post("/api/demo/pair")
    def demo_pair(request: Request):
        if not demo_mode:
            raise HTTPException(status_code=404, detail="Not found")
        customer, staff = new_demo_workspace(request)
        return {"customer": session_for(customer), "staff": session_for(staff)}

    @app.post("/api/auth/google")
    def google_sign_in(body: GoogleRequest, request: Request):
        if not google_client_id:
            raise HTTPException(status_code=404, detail="Not found")
        enforce_limit("login", client_ip(request))
        try:
            claims = verify_google(body.credential, google_client_id)
        except ValueError:
            raise HTTPException(status_code=401, detail="Google sign-in could not be verified. Please try again.")
        email = str(claims.get("email") or "").lower()
        if not email or not claims.get("email_verified"):
            raise HTTPException(status_code=401, detail="Google did not confirm this email address.")
        user = store.find_or_create_customer(email, str(claims.get("name") or email.split("@")[0]))
        # Staff access must never come from a Google account.
        if user["role"] != "customer":
            raise HTTPException(status_code=403, detail="Support team accounts sign in with a password.")
        return session_for(user)

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return public_user(user)

    @app.post("/api/me/password")
    def change_password(body: PasswordChange, user=Depends(current_user)):
        if user["workspace"] is not None:
            raise HTTPException(status_code=403, detail="Demo accounts have no password.")
        if user["has_password"] and store.authenticate(user["email"], body.current_password or "") is None:
            raise HTTPException(status_code=400, detail="Your current password is not correct.")
        store.set_password(user["email"], body.new_password)
        return {"ok": True}

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request, user=Depends(current_customer)):
        enforce_chat_limits(request, user)
        conversation = conversation_for_chat(body, user["email"])
        return turn_view(conversation, agent.handle_message(conversation, body.message))

    @app.post("/api/chat/stream")
    def chat_stream(body: ChatRequest, request: Request, user=Depends(current_customer)):
        """Same as /api/chat, with one JSON line per real agent step before the final result."""
        enforce_chat_limits(request, user)
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

    @app.get("/api/staff/escalations")
    def escalations(staff=Depends(current_staff)):
        return store.list_escalations(staff["workspace"])

    @app.get("/api/staff/conversations/{conversation_id}")
    def staff_conversation(conversation_id: str, staff=Depends(current_staff)):
        conversation = staff_conversation_or_404(conversation_id, staff)
        return {
            **conversation_view(conversation),
            "customer_email": conversation["customer_email"],
            "tool_calls": store.get_tool_calls(conversation_id),
        }

    @app.post("/api/staff/conversations/{conversation_id}/reply")
    def staff_reply(conversation_id: str, body: StaffReply, staff=Depends(current_staff)):
        enforce_limit("staff_reply", staff["email"])
        staff_conversation_or_404(conversation_id, staff)
        store.add_message(conversation_id, "staff", body.message)
        return {"ok": True}

    @app.post("/api/staff/escalations/{escalation_id}/resolve")
    def resolve(escalation_id: int, staff=Depends(current_staff)):
        if not store.resolve_escalation(escalation_id, staff["workspace"]):
            raise HTTPException(status_code=404, detail="Open escalation not found")
        return {"ok": True}

    return app
