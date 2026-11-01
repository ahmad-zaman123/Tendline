import json
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import AfterValidator, BaseModel, EmailStr, Field, StringConstraints

from .agent import HANDOFF_REPLY, SupportAgent
from .config import load_settings
from .docs_index import DocsIndex
from .llm import GeminiClient, GeminiEmbedder
from .mailer import smtp_sender
from .store import Store

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(hours=8)
# Shown on the sign-in page. They open a private workspace, never a shared account.
DEMO_LOGINS = {"demo@tendline.app": "customer", "support@tendline.app": "staff"}
DEMO_LOGIN_PASSWORD = "demo1234"
SAMPLE_REQUEST = "I want a refund for order A1001."
SAMPLE_SUMMARY = "Customer requested a refund for order A1001."
BUSY = "Too many requests. Please try again in a little while."
RESET_SENT = "If that email has an account, we've sent a reset link."
RESET_INVALID = "This reset link is invalid or has expired."
# Name: (allowed hits, window, message). Kept in Postgres because serverless instances share no memory.
RATE_LIMITS = {
    "login": (20, timedelta(minutes=15), "Too many sign-in attempts. Please try again in a few minutes."),
    "register": (10, timedelta(hours=1), BUSY),
    "register_all": (500, timedelta(days=1), "Sign-ups are full for today. Please try again tomorrow."),
    "reset_ip": (10, timedelta(hours=1), BUSY),
    "reset_email": (3, timedelta(hours=1), BUSY),
    "reset_all": (200, timedelta(days=1), "Password resets are unavailable for today. Please try again tomorrow."),
    "reset_use": (20, timedelta(hours=1), BUSY),
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


def storable(value):
    # Postgres can't hold NUL characters, and lone surrogates can't be encoded at all.
    if any(c == "\x00" or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("contains characters that can't be stored")
    return value


StorableText = Annotated[str, AfterValidator(storable)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000), AfterValidator(storable)]


class LoginRequest(BaseModel):
    email: EmailStr
    password: StorableText = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    email: EmailStr
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100), AfterValidator(storable)]
    password: StorableText = Field(min_length=8, max_length=200)


class ForgotPassword(BaseModel):
    email: EmailStr


class ResetPassword(BaseModel):
    token: StorableText = Field(min_length=1, max_length=200)
    new_password: StorableText = Field(min_length=8, max_length=200)


class GoogleRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=8000)


class PasswordChange(BaseModel):
    current_password: StorableText | None = Field(default=None, max_length=200)
    new_password: StorableText = Field(min_length=8, max_length=200)


class DemoSessionRequest(BaseModel):
    role: Literal["customer", "staff"]


class ChatRequest(BaseModel):
    message: Message
    conversation_id: StorableText | None = Field(default=None, max_length=64)


class StaffReply(BaseModel):
    message: Message


def public_user(user):
    return {
        "email": user["email"],
        "role": user["role"],
        "name": user["name"],
        "has_password": user["has_password"],
        "demo": user["workspace"] is not None and not user["permanent"],
        "can_switch": user["workspace"] is not None and user["permanent"],
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
    cors_origins=(),
    send_email=None,
    app_url="http://localhost:5173",
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
        cors_origins = settings["cors_origins"]
        app_url = settings["app_url"]
        if settings["smtp"]:
            send_email = smtp_sender(**settings["smtp"])

    agent = SupportAgent(llm, store, DocsIndex(store, embedder))
    app = FastAPI(title="Tendline", docs_url="/api/docs", openapi_url="/api/openapi.json")
    # Sessions use a bearer token rather than cookies, so credentials stay off.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After"],
    )

    @app.exception_handler(RequestValidationError)
    def invalid_request(request, error):
        # FastAPI's default repeats the input, which can be a password or text that can't be encoded.
        detail = [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    def session_for(user):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": user["email"], "iat": now, "exp": now + TOKEN_LIFETIME},
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
        # Whole seconds, like iat, so a token issued in the same second as the change still works.
        changed = user["password_changed_at"]
        if changed and claims.get("iat", 0) < int(changed.timestamp()):
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

    def conversation_for_chat(body, request, user):
        email = user["email"]
        if body.conversation_id:
            conversation = owned_conversation(body.conversation_id, email)
        else:
            # A second tab or an old page must not start a chat that hides the team's reply.
            latest = store.latest_conversation(email)
            conversation = latest if latest and latest["status"] == "escalated" else None
        enforce_limit("chat_user", email)
        enforce_limit("chat_ip", client_ip(request))
        # The model stays out of a hand-off, so the budget for its calls doesn't apply.
        if conversation is None or conversation["status"] != "escalated":
            enforce_limit("chat_all", "all")
        return conversation or owned_conversation(store.create_conversation(email), email)

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

    @app.post("/api/register")
    def register(body: RegisterRequest, request: Request):
        enforce_limit("register", client_ip(request))
        enforce_limit("register_all", "all")
        email = body.email.lower()
        # The demo logins and the generated workspace accounts all live on this domain.
        if email.endswith("@tendline.app"):
            raise HTTPException(status_code=400, detail="This email address can't be used.")
        user = store.register(email, body.name, body.password)
        if user is None:
            raise HTTPException(status_code=409, detail="An account with this email already exists. Try signing in.")
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
        if user["workspace"] is not None and (not user["permanent"] or user["role"] == "staff"):
            raise HTTPException(status_code=403, detail="This account has no password.")
        if user["has_password"] and store.authenticate(user["email"], body.current_password or "") is None:
            raise HTTPException(status_code=400, detail="Your current password is not correct.")
        store.set_password(user["email"], body.new_password)
        # Every older session just ended, so this one needs a new token to carry on.
        return session_for(store.get_user(user["email"]))

    @app.post("/api/password/forgot")
    def forgot_password(body: ForgotPassword, request: Request):
        if send_email is None:
            raise HTTPException(status_code=503, detail="Password reset isn't available right now.")
        enforce_limit("reset_ip", client_ip(request))
        enforce_limit("reset_all", "all")
        email = body.email.lower()
        # Over this limit nothing is sent, but the reply stays the same for every address.
        limit, window, _ = rate_limits["reset_email"]
        token = None if store.hit_rate_limit(f"reset_email:{email}", limit, window) else store.create_reset_token(email)
        if token:
            try:
                send_email(
                    email,
                    "Reset your Tendline password",
                    "Someone asked to reset the password for this Tendline account. "
                    "The link expires in 15 minutes. If it wasn't you, you can ignore this email.",
                    "Choose a new password",
                    f"{app_url}/reset-password?token={token}",
                )
            except Exception:
                logger.exception("Could not send a password reset email")
        return {"detail": RESET_SENT}

    @app.post("/api/password/reset")
    def reset_password(body: ResetPassword, request: Request):
        enforce_limit("reset_use", client_ip(request))
        if not store.reset_password(body.token, body.new_password):
            raise HTTPException(status_code=400, detail=RESET_INVALID)
        return {"ok": True}

    @app.post("/api/me/switch")
    def switch_view(user=Depends(current_user)):
        partner = store.workspace_partner(user) if user["workspace"] is not None and user["permanent"] else None
        if partner is None:
            raise HTTPException(status_code=404, detail="Not found")
        return session_for(partner)

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request, user=Depends(current_customer)):
        conversation = conversation_for_chat(body, request, user)
        return turn_view(conversation, agent.handle_message(conversation, body.message))

    @app.post("/api/chat/stream")
    def chat_stream(body: ChatRequest, request: Request, user=Depends(current_customer)):
        """Same as /api/chat, with one JSON line per real agent step before the final result."""
        conversation = conversation_for_chat(body, request, user)
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
