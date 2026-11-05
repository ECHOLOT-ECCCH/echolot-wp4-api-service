"""LLM provider abstraction.

One implementation (Anthropic) for now. `get_provider` takes explicit overrides so a
request can bring its own key and model.
"""

import os
from typing import Any, Protocol, TypeVar

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

T = TypeVar("T", bound=BaseModel)

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"  # low | medium | high | xhigh | max
MAX_OUTPUT_TOKENS = 16000  # the SDK's ceiling for a non-streaming request
REQUEST_TIMEOUT_SECONDS = 300.0
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Callers can choose any model, but only these are known to accept `fallbacks: "default"`.
# Haiku 5.5, for one, has no server-side fallback.
REFUSAL_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)

# JSON-schema keywords the structured-outputs API rejects. Pydantic emits some of them
# (e.g. `default`); constraints like `minimum` would appear if a Field() ever adds them.
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


class LLMError(Exception):
    """The LLM call failed or produced unusable output."""


class LLMNotConfigured(LLMError):
    """No API key, or an unknown provider, is configured."""


class LLMRefused(LLMError):
    """The model declined the request."""


class LLMTruncated(LLMError):
    """The output hit the token limit before completing."""


class LLMKeyRejected(LLMError):
    """The provider rejected the API key, or the key lacks permission."""


class LLMRequestRejected(LLMError):
    """The provider rejected the request, e.g. an unknown model or an unsupported option."""


class LLMRateLimited(LLMError):
    """The provider's rate limit was reached and the client's retries ran out."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_structured(
        self, *, system: str, user: str, schema: type[T]
    ) -> tuple[T, dict[str, int]]:
        """Run one prompt and return (validated instance of `schema`, token usage)."""


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        refusal_fallback: bool = True,
    ):
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self._client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)

    def complete_structured(
        self, *, system: str, user: str, schema: type[T]
    ) -> tuple[T, dict[str, int]]:
        # `messages.create` + own validation rather than the SDK's `parse` helper: `parse`
        # validates the text before the caller can look at stop_reason, so a refusal or a
        # truncated reply surfaces as a JSON error instead of a clear message.
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "format": {"type": "json_schema", "schema": strict_json_schema(schema)},
                "effort": self.effort,
            },
        }
        try:
            if self.refusal_fallback and self.model in REFUSAL_FALLBACK_MODELS:
                # A safety-declined request is re-run server-side on Anthropic's
                # recommended fallback model instead of coming back as a refusal.
                response = self._client.beta.messages.create(
                    **params, betas=[REFUSAL_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMKeyRejected(f"Anthropic rejected the API key: {exc}") from exc
        except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
            raise LLMRequestRejected(f"Anthropic rejected the request: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimited(f"Anthropic rate limit reached: {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc

        # Report the model that answered, which differs from the requested one after a fallback.
        self.model = response.model

        if response.stop_reason == "refusal":
            details = response.stop_details
            reason = (
                getattr(details, "explanation", None)
                or getattr(details, "category", None)
                or "no reason given"
            )
            raise LLMRefused(f"model declined the request ({reason})")
        if response.stop_reason == "max_tokens":
            raise LLMTruncated(f"output exceeded {MAX_OUTPUT_TOKENS} tokens")

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMError(f"model output did not match the expected schema: {exc}") from exc
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return parsed, usage


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema in the form structured outputs accept.

    Every object is closed (`additionalProperties: false`) and unsupported keywords are
    dropped. Nested models stay as `$defs` + `$ref`, which the API supports.
    """
    return _strictify(model.model_json_schema())


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_SCHEMA_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            out[key] = {name: _strictify(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = _strictify(value)
        elif key in ("anyOf", "oneOf", "allOf"):
            out[key] = [_strictify(sub) for sub in value]
        else:
            out[key] = value
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def get_provider(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> LLMProvider:
    """Build a provider from explicit arguments, falling back to the environment."""
    provider_name = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
    effort = effort or os.getenv("LLM_EFFORT") or DEFAULT_EFFORT

    if provider_name == "anthropic":
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise LLMNotConfigured("send an X-LLM-API-Key header or set ANTHROPIC_API_KEY on the server")
        return AnthropicProvider(
            api_key=key,
            model=model,
            effort=effort,
            refusal_fallback=_env_flag("LLM_REFUSAL_FALLBACK", default=True),
        )
    raise LLMNotConfigured(f"unknown LLM provider {provider_name!r}")


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")
