import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def load_settings():
    load_dotenv(BASE_DIR / ".env")
    missing = [n for n in ("DATABASE_URL", "JWT_SECRET", "GEMINI_API_KEY") if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(missing)}")
    chat_model = os.environ.get("GEMINI_CHAT_MODEL", "models/gemini-3.5-flash-lite")
    fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "models/gemini-3.1-flash-lite")
    # Each model has its own daily limit, so these keep the demo answering after the first two run out.
    extra_models = ("models/gemini-2.5-flash-lite", "models/gemini-2.5-flash")
    cors_origins = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    return {
        "database_url": os.environ["DATABASE_URL"],
        "jwt_secret": os.environ["JWT_SECRET"],
        "gemini_api_key": os.environ["GEMINI_API_KEY"],
        "chat_models": list(dict.fromkeys(m for m in (chat_model, fallback_model, *extra_models) if m)),
        "demo_mode": os.environ.get("DEMO_MODE") == "1",
        "google_client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "cors_origins": cors_origins,
        # Reset links point here. Taken from settings because a request's own headers can be forged.
        "app_url": cors_origins[0] if cors_origins else "http://localhost:5173",
        "smtp": smtp_user and smtp_password and {
            "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            "port": int(os.environ.get("SMTP_PORT", "587")),
            "user": smtp_user,
            "password": smtp_password,
            "sender": os.environ.get("EMAIL_FROM") or smtp_user,
        },
        # Vercel sets the client address itself. Elsewhere the header could be forged.
        "behind_proxy": bool(os.environ.get("VERCEL")),
    }
