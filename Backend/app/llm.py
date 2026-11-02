import logging
import time
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import errors, types

from .store import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_MS = 15_000
# No new model starts once this much of the reply's time has gone.
MODEL_DEADLINE_SECONDS = 20
EMBEDDING_ATTEMPTS = 2
EMBEDDING_MODEL = "models/gemini-embedding-001"
# Retired model, or this model's rate limit. A bad key or request would fail on any model.
FALLBACK_CLIENT_ERRORS = {404, 429}


def _client(api_key, attempts):
    # A customer is waiting, so fail fast and let the agent hand off to a person.
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=attempts),
        ),
    )


def _can_use_fallback(error):
    if isinstance(error, errors.ClientError):
        return error.code in FALLBACK_CLIENT_ERRORS
    return isinstance(error, (errors.ServerError, httpx.TransportError))


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    raw: object = None


class GeminiClient:
    """Thin wrapper over the google-genai SDK. The agent runs its own tool loop."""

    def __init__(self, api_key, models):
        # Each model gets one try, because the next model is a faster recovery than a retry.
        self.client = _client(api_key, attempts=1)
        self.models = models

    def _generate(self, contents, config):
        last_model = self.models[-1]
        started = time.monotonic()
        for model in self.models:
            try:
                return self.client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except Exception as error:
                out_of_time = time.monotonic() - started >= MODEL_DEADLINE_SECONDS
                if model == last_model or out_of_time or not _can_use_fallback(error):
                    raise
                logger.warning("Model %s failed (%s), trying the next one", model, error)

    def complete(self, system, history, tools):
        response = self._generate(
            contents=[self._to_content(entry) for entry in history],
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=tools)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        calls =[ToolCall(c.name, dict(c.args or {})) for c in response.function_calls or []]
        return LLMReply(
            text=response.text or "" if not calls else "",
            tool_calls=calls,
            raw=response.candidates[0].content,
        )

    @staticmethod
    def _to_content(entry):
        if entry["role"] == "model_raw":
            # Sent back as received so Gemini keeps its own function-call metadata.
            return entry["raw"]
        if entry["role"] == "tool_results":
            parts = [
                types.Part.from_function_response(name=name, response=result)
                for name, result in entry["results"]
            ]
            return types.Content(role="user", parts=parts)
        role = "user" if entry["role"] == "user" else "model"
        return types.Content(role=role, parts=[types.Part.from_text(text=entry["text"])])


class GeminiEmbedder:
    def __init__(self, api_key):
        self.client = _client(api_key, attempts=EMBEDDING_ATTEMPTS)

    def _embed(self, texts, task_type):
        response = self.client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=EMBEDDING_DIMENSIONS
            ),
        )
        return [embedding.values for embedding in response.embeddings]

    def embed_documents(self, texts):
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0]
