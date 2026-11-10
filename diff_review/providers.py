"""Model providers for the review pipeline.

- ``anthropic`` (default): the Claude API.
- ``openai``: any OpenAI-compatible endpoint. That is the OpenAI API itself,
  or a self-hosted server via ``--base-url`` (llama.cpp ``llama-server``,
  vLLM, LocalAI, Jan, ...).
- ``ollama`` / ``lmstudio``: the ``openai`` provider preset to the local
  server's default address, so no base URL or API key is needed.

Selection order: ``configure()`` arguments (set from CLI flags), then the
``DIFF_REVIEW_PROVIDER`` environment variable, then ``anthropic``.

Providers never put a raw API error in their exceptions: provider error
bodies can echo request content, which is the (possibly sensitive) diff.
"""

import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
# Non-streaming ceiling that stays under SDK HTTP timeouts. Current Claude
# models think adaptively and thinking counts against this budget, so it is
# set well above what a JSON review needs.
MAX_TOKENS = 16_000

PROVIDERS = ("anthropic", "openai", "ollama", "lmstudio")
REASONING_LEVELS = ("none", "low", "medium", "high")

# USD per million tokens (input, output); first-party Claude API list prices.
# Unknown models get no cost estimate rather than a wrong one.
_ANTHROPIC_PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Models that accept the server-side refusal fallback ("default" form).
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_LOCAL_PRESETS = {
    "ollama": lambda: _ollama_base_url(),
    "lmstudio": lambda: "http://localhost:1234/v1",
}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")


# Error bodies are never printed (they can echo the diff), so say what a status means.
_STATUS_HINTS = {
    400: "; check the model name and the account's credit balance",
    401: "; check ANTHROPIC_API_KEY",
    403: "; the key lacks access to this model",
    404: "; unknown model",
    429: "; rate limited, retry later or lower --concurrency",
    529: "; the API is overloaded, retry later",
}


class ProviderError(RuntimeError):
    """Provider misconfiguration or a failed API request (safe to print)."""


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    complete: bool = True
    refusal: str | None = None  # set when the model declined; holds the category


@dataclass
class Pricing:
    input_per_token: float
    output_per_token: float


def _ollama_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = "http://" + host
    host = host.rstrip("/").replace("://0.0.0.0", "://localhost")
    return host if host.endswith("/v1") else host + "/v1"


def _is_local(base_url: str | None) -> bool:
    if not base_url:
        return False
    host = re.sub(r"^[a-z]+://", "", base_url.lower()).split("/", 1)[0]
    host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return host in _LOCAL_HOSTS or host.endswith(".local") or host.startswith(("10.", "192.168.", "100."))


@lru_cache(maxsize=1)
def _anthropic_client():
    import anthropic

    return anthropic.Anthropic()


class AnthropicProvider:
    name = "anthropic"
    default_concurrency = 8

    def __init__(self, model: str | None = None, reasoning: str | None = None):
        if reasoning == "none":
            raise ProviderError(
                "Current Claude models cannot turn thinking off; use --reasoning low instead."
            )
        self._effort = reasoning
        if not (os.environ.get("ANTHROPIC_API_KEY", "").strip()
                or os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()):
            raise ProviderError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Or use a local model: --provider ollama --model <name>."
            )
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL
        price = _ANTHROPIC_PRICES.get(self.model)
        self.pricing = Pricing(price[0] / 1e6, price[1] / 1e6) if price else None
        # Server-side fallback is a Claude API feature; a custom base URL may
        # be a proxy or another platform that rejects the beta parameter.
        self._fallback = (self.model in _FALLBACK_MODELS
                          and not os.environ.get("ANTHROPIC_BASE_URL", "").strip())

    def complete(self, system: str, user: str, max_tokens: int = MAX_TOKENS,
                 json_schema: dict | None = None) -> Completion:
        # Claude follows the in-prompt schema reliably; json_schema is for
        # servers that need constrained decoding.
        import anthropic

        request = dict(model=self.model, max_tokens=max_tokens, system=system,
                       messages=[{"role": "user", "content": user}])
        if self._effort:
            request["output_config"] = {"effort": self._effort}
        client = _anthropic_client()
        try:
            if self._fallback:
                # A policy decline is retried on a fallback model server side.
                response = client.beta.messages.create(
                    **request, betas=[_FALLBACK_BETA], fallbacks="default")
            else:
                response = client.messages.create(**request)
        except anthropic.APIStatusError as exc:
            hint = _STATUS_HINTS.get(exc.status_code, "")
            raise ProviderError(f"Claude API request failed (HTTP {exc.status_code}){hint}") from None
        except anthropic.APIConnectionError:
            raise ProviderError("Could not connect to the Claude API") from None
        except anthropic.APIError:
            raise ProviderError("Claude API request failed") from None
        usage = response.usage
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return Completion("", usage.input_tokens, usage.output_tokens, complete=False,
                              refusal=getattr(details, "category", None) or "unspecified")
        # Skip thinking and other non-text blocks.
        text = "".join(getattr(block, "text", None) or "" for block in response.content
                       if getattr(block, "type", "text") == "text")
        return Completion(text, usage.input_tokens, usage.output_tokens,
                          complete=response.stop_reason == "end_turn" and bool(text))


class OpenAIProvider:
    name = "openai"

    # No default model: there is no single obvious choice across the OpenAI
    # API and local servers, and a wrong guess fails with a confusing 404.
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 preset: str | None = None, reasoning: str | None = None):
        try:
            import openai
        except ImportError:
            raise ProviderError(
                "The openai package is not installed. "
                "Install it with: pip install 'diff-review[openai]'"
            ) from None
        if preset:
            self.name = preset
        self.model = model or os.environ.get("OPENAI_MODEL", "").strip()
        if not self.model:
            hint = "ollama list" if preset == "ollama" else "the server's model list"
            raise ProviderError(
                f"No model set for the {self.name} provider. Pass --model or set "
                f"OPENAI_MODEL (see {hint}; e.g. qwen3.8:27b-q4_K_M)."
            )
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "").strip()
                         or (_LOCAL_PRESETS[preset]() if preset else None))
        # Self-hosted servers accept any key; only OpenAI itself needs one.
        api_key = os.environ.get("OPENAI_API_KEY", "").strip() or (
            "not-needed" if self.base_url else None)
        if api_key is None:
            raise ProviderError(
                "OPENAI_API_KEY is not set. Set it, or use a local server: "
                "--provider ollama, --provider lmstudio, or --base-url URL."
            )
        self._openai = openai
        timeout = float(os.environ.get("DIFF_REVIEW_TIMEOUT", "") or (1800 if self.base_url else 600))
        self._client = openai.OpenAI(base_url=self.base_url, api_key=api_key, timeout=timeout)
        # A local server usually runs one generation at a time; parallel
        # requests just queue and time out while waiting.
        self.default_concurrency = 1 if _is_local(self.base_url) else 8
        self.pricing = None  # varies by model/server; report tokens without cost
        # Constrained decoding, degraded on a 400: json_schema > json_object > none.
        self._format_modes = ["json_schema", "json_object", None]
        self._lock = threading.Lock()
        self._token_param = "max_tokens"
        # Thinking models (qwen3, gpt-oss, deepseek-r1, ...) honor
        # reasoning_effort on Ollama and OpenAI; "none" is much faster locally.
        self._reasoning = reasoning

    def _create(self, system: str, user: str, max_tokens: int, json_schema: dict | None):
        while True:
            kwargs = {self._token_param: max_tokens}
            mode = next(m for m in self._format_modes  # ends in None: always found
                        if not (m == "json_schema" and json_schema is None))
            if mode == "json_schema":
                kwargs["response_format"] = {"type": "json_schema",
                                             "json_schema": {"name": "review", "schema": json_schema}}
            elif mode:
                kwargs["response_format"] = {"type": mode}
            if self._reasoning:
                kwargs["reasoning_effort"] = self._reasoning
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    **kwargs,
                )
            except self._openai.BadRequestError as exc:
                message = str(exc)
                # Newer OpenAI models want max_completion_tokens; local servers
                # generally only know max_tokens.
                if self._token_param == "max_tokens" and "max_completion_tokens" in message:
                    self._token_param = "max_completion_tokens"
                # Not every server supports structured output; prompts still ask for JSON.
                elif mode and ("response_format" in message or "json_schema" in message):
                    # Parallel workers can hit the same rejection at once.
                    with self._lock:
                        if mode in self._format_modes:
                            self._format_modes.remove(mode)
                elif self._reasoning and "reasoning_effort" in message:
                    self._reasoning = None
                else:
                    raise

    def complete(self, system: str, user: str, max_tokens: int = MAX_TOKENS,
                 json_schema: dict | None = None) -> Completion:
        try:
            response = self._create(system, user, max_tokens, json_schema)
        except self._openai.APIStatusError as exc:
            hint = ""
            if exc.status_code == 404 and self.base_url:
                hint = f"; is model {self.model!r} installed on the server?"
            raise ProviderError(f"{self.name} request failed (HTTP {exc.status_code}){hint}") from None
        except self._openai.APIConnectionError:
            where = self.base_url or "the OpenAI API"
            raise ProviderError(f"Could not connect to {where}; is the server running?") from None
        except self._openai.OpenAIError:
            raise ProviderError(f"{self.name} request failed") from None
        usage = response.usage
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        if not response.choices:
            return Completion("", tokens_in, tokens_out, complete=False)
        choice = response.choices[0]
        text = choice.message.content or ""
        refusal = getattr(choice.message, "refusal", None)
        if refusal or choice.finish_reason == "content_filter":
            return Completion("", tokens_in, tokens_out, complete=False, refusal="content_filter")
        # Some servers omit finish_reason; only an explicit cutoff is incomplete.
        return Completion(text, tokens_in, tokens_out,
                          complete=choice.finish_reason != "length" and bool(text.strip()))


_config: dict = {"provider": None, "model": None, "base_url": None, "reasoning": None}
_provider = None


def configure(provider: str | None = None, model: str | None = None,
              base_url: str | None = None, reasoning: str | None = None) -> None:
    global _provider
    _config.update(provider=provider, model=model, base_url=base_url, reasoning=reasoning)
    _provider = None


def provider_name() -> str:
    return (_config["provider"] or os.environ.get("DIFF_REVIEW_PROVIDER", "").strip().lower()
            or "anthropic")


def get_provider():
    global _provider
    if _provider is None:
        name = provider_name()
        reasoning = (_config["reasoning"]
                     or os.environ.get("DIFF_REVIEW_REASONING", "").strip().lower() or None)
        if reasoning and reasoning not in REASONING_LEVELS:
            raise ProviderError(
                f"Unknown reasoning level {reasoning!r}; expected one of: {', '.join(REASONING_LEVELS)}."
            )
        if name == "anthropic":
            _provider = AnthropicProvider(model=_config["model"], reasoning=reasoning)
        elif name in ("openai", *_LOCAL_PRESETS):
            _provider = OpenAIProvider(model=_config["model"], base_url=_config["base_url"],
                                       preset=name if name in _LOCAL_PRESETS else None,
                                       reasoning=reasoning)
        else:
            raise ProviderError(
                f"Unknown provider {name!r}; expected one of: {', '.join(PROVIDERS)}."
            )
    return _provider
