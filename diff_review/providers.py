"""Model providers for the review pipeline.

Two providers are supported:

- ``anthropic`` (default) — the Claude API, configured exactly as before.
- ``openai`` — any OpenAI-compatible endpoint: the OpenAI API itself, or a
  local server such as Ollama (``--base-url http://localhost:11434/v1``).

Selection order: ``configure()`` arguments (set from CLI flags), then the
``DIFF_REVIEW_PROVIDER`` environment variable, then ``anthropic``.
"""

import os
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """Raised for provider misconfiguration or API failures."""


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass
class Pricing:
    input_per_token: float
    output_per_token: float


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.pricing = Pricing(3.0 / 1_000_000, 15.0 / 1_000_000)

    def complete(self, system: str, user: str, max_tokens: int = 8192) -> Completion:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except self._anthropic.APIError as e:
            raise ProviderError(f"Anthropic API error: {e}") from e
        if not response.content or response.stop_reason == "max_tokens":
            raise ProviderError(
                f"Incomplete API response (stop_reason={response.stop_reason!r})"
            )
        return Completion(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAIProvider:
    name = "openai"

    # No default model: unlike Anthropic there is no single obvious choice
    # across the OpenAI API and local servers, and a wrong guess fails with a
    # confusing 404 instead of a clear message.
    def __init__(self, model: str | None = None, base_url: str | None = None):
        try:
            import openai
        except ImportError as e:
            raise ProviderError(
                "The openai package is not installed. "
                "Install it with: pip install 'diff-review[openai]'"
            ) from e

        self.model = model or os.environ.get("OPENAI_MODEL", "").strip()
        if not self.model:
            raise ProviderError(
                "No model set for the openai provider. "
                "Pass --model or set OPENAI_MODEL (e.g. qwen3.6:27b for Ollama)."
            )
        base_url = base_url or os.environ.get("OPENAI_BASE_URL", "").strip() or None
        # Local OpenAI-compatible servers accept any key; only a real OpenAI
        # endpoint needs a genuine one.
        api_key = os.environ.get("OPENAI_API_KEY", "").strip() or (
            "not-needed" if base_url else None
        )
        if api_key is None:
            raise ProviderError(
                "OPENAI_API_KEY is not set. Set it, or pass --base-url for a "
                "local OpenAI-compatible server (e.g. http://localhost:11434/v1)."
            )
        self._openai = openai
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self.pricing = None  # varies by model/server; report tokens without cost

    def _create(self, system: str, user: str, max_tokens: int):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            return self._client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
            )
        except self._openai.BadRequestError as e:
            # Newer OpenAI models reject max_tokens in favor of
            # max_completion_tokens; local servers generally only know
            # max_tokens, so retry rather than switch unconditionally.
            if "max_completion_tokens" not in str(e):
                raise
            return self._client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=messages,
            )

    def complete(self, system: str, user: str, max_tokens: int = 8192) -> Completion:
        try:
            response = self._create(system, user, max_tokens)
        except self._openai.OpenAIError as e:
            raise ProviderError(f"OpenAI-compatible API error: {e}") from e
        if not response.choices or response.choices[0].finish_reason == "length":
            finish = response.choices[0].finish_reason if response.choices else None
            raise ProviderError(f"Incomplete API response (finish_reason={finish!r})")
        usage = response.usage
        return Completion(
            text=response.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


_config: dict = {"provider": None, "model": None, "base_url": None}
_provider = None


def configure(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> None:
    global _provider
    _config.update(provider=provider, model=model, base_url=base_url)
    _provider = None


def provider_name() -> str:
    return _config["provider"] or os.environ.get("DIFF_REVIEW_PROVIDER", "anthropic")


def get_provider():
    global _provider
    if _provider is None:
        name = provider_name()
        if name == "anthropic":
            _provider = AnthropicProvider(model=_config["model"])
        elif name == "openai":
            _provider = OpenAIProvider(
                model=_config["model"], base_url=_config["base_url"]
            )
        else:
            raise ProviderError(
                f"Unknown provider {name!r} — expected 'anthropic' or 'openai'."
            )
    return _provider
