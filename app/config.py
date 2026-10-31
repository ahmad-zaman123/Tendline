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
    return {
        "database_url": os.environ["DATABASE_URL"],
        "jwt_secret": os.environ["JWT_SECRET"],
        "gemini_api_key": os.environ["GEMINI_API_KEY"],
        "chat_models": list(dict.fromkeys(m for m in (chat_model, fallback_model) if m)),
        "demo_mode": os.environ.get("DEMO_MODE") == "1",
        # Vercel sets the client address itself. Elsewhere the header could be forged.
        "behind_proxy": bool(os.environ.get("VERCEL")),
    }
