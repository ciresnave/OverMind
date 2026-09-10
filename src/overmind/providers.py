"""Provider client — one OpenAI-shaped surface over five free-tier backends.

Every behaviour here is a measured one (MEASUREMENTS.md §12, §12.1, §12.2).
Nothing is defensive-in-general; each guard names the observation that requires it.

WHAT WAS MEASURED, AND WHAT EACH FACT COSTS IF IGNORED:

  · All five providers reach a verified tool call. Four are a pure base-URL +
    key swap. Cloudflare needs exactly one message normalisation.

  · ⚠️ THE ASSISTANT MESSAGE AN API HANDS YOU IS NOT ALWAYS A VALID MESSAGE TO
    HAND BACK. A tool-call message carries `content: null`; four providers
    tolerate it and Cloudflare rejects it with a schema-path error that does not
    name the cause. A client that echoes verbatim works on four integrations and
    breaks on the fifth. `normalise_for_echo` is that fix.

  · ⚠️ ROSTERS OVER-REPORT. NVIDIA serves 17 of 80 rostered models to this
    account - a naive pick from /models fails about four times in five, and each
    failure reads like a provider verdict. Google lists models that 404 with
    "no longer available to new users". So model choice is CANDIDATES, tried in
    order, never a single guess.

  · ⚠️ FREE AVAILABILITY IS PER-MODEL AND TRANSIENT. OpenRouter 429'd two free
    models and served a third in the same run. Fail over; never pin one.

  · ⚠️ A 403 AT THE EDGE AND A 403 FROM THE API ARE DIFFERENT SUBJECTS WEARING
    ONE STATUS CODE. Groq's Cloudflare edge blocks urllib's default User-Agent
    with "error code: 1010", which reads exactly like a rejected key.

  · ⚠️ "REACHABLE" AND "RETURNS" ARE DIFFERENT PROPERTIES. One NVIDIA model
    routed and never answered - 240 s, twice. Every call carries a timeout.

  · ⚠️ A THINKING MODEL SPENDS THE OUTPUT BUDGET BEFORE IT ANSWERS. At
    max_tokens=160 gemini-3.6-flash returned 2-8 visible characters, which read
    as "the model does not hold its rules" and was my own instrument.

  · ⚠️ KEYS LIVE UNDER NON-CONVENTIONAL NAMES, AND A LONG-RUNNING PROCESS DOES
    NOT INHERIT ONES SET AFTER IT STARTED. The environment a process holds is a
    snapshot, not a reading - so the registry is consulted first on Windows.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Provider", "PROVIDERS", "ProviderClient", "ChatResult", "Usage", "ProviderError",
    "RateLimited", "NoUsableModel", "read_secret", "normalise_for_echo",
    "select_models",
]

USER_AGENT = "OverMind/0.1 (+https://github.com/ciresnave/OverMind)"


class ProviderError(RuntimeError):
    def __init__(self, provider: str, status: int | None, body: str) -> None:
        super().__init__(f"{provider}: HTTP {status}: {body[:300]}")
        self.provider = provider
        self.status = status
        self.body = body


class RateLimited(ProviderError):
    """429. ⚠️ NOT a model verdict and never scored as behaviour."""


class NoUsableModel(RuntimeError):
    """Every candidate was refused, unentitled or rate-limited.

    ⚠️ Carries the per-model reasons, because "no model worked" and "this
    account is entitled to none of the models I guessed" need different fixes
    and are indistinguishable from a bare failure.
    """

    def __init__(self, provider: str, attempts: Sequence[tuple[str, str]]) -> None:
        detail = "; ".join(f"{m}: {why}" for m, why in attempts)
        super().__init__(f"{provider}: no usable model. Tried - {detail}")
        self.provider = provider
        self.attempts = list(attempts)


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #

def read_secret(name: str) -> str | None:
    """Read a persistent user secret.

    ⚠️ REGISTRY FIRST ON WINDOWS. This process may predate the variable, and
    `os.environ` would then report absent for something that exists. Measured:
    all five provider keys were invisible to a running session.
    """
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if value:
                    return str(value)
        except (OSError, ImportError):
            pass
    return os.environ.get(name)


# --------------------------------------------------------------------------- #
# Provider table
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Provider:
    key: str
    base_url: str
    secret_name: str
    #: Ordered preferences. Candidates are tried in order; the roster filters them.
    prefer: tuple[str, ...] = ()
    #: Explicit fallbacks used when the roster is unavailable (Cloudflare has no
    #: OpenAI-shaped /models: it answers 405).
    fallback_models: tuple[str, ...] = ()
    #: ⚠️ Cloudflare rejects `content: null` on an echoed assistant message.
    strict_message_schema: bool = False
    #: Cloudflare's OpenAI-compatible path is account-scoped.
    account_secret_name: str | None = None
    #: OpenRouter asks for attribution headers.
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    #: Roster endpoint relative to base_url, or None when there is not one.
    models_path: str | None = "/models"

    def resolved_base(self) -> str:
        base = self.base_url
        if self.account_secret_name:
            account = read_secret(self.account_secret_name)
            if not account:
                raise ProviderError(self.key, None,
                                    f"{self.account_secret_name} is not set; "
                                    f"{self.key} needs an account id in its path")
            base = base.replace("{account}", account)
        return base.rstrip("/")


PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        key="groq",
        base_url="https://api.groq.com/openai/v1",
        secret_name="GROQ_CLOUD_API_TOKEN",
        prefer=("qwen", "llama-3.3", "gpt-oss"),
    ),
    "google": Provider(
        key="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        secret_name="GOOGLE_AI_STUDIO_API_TOKEN",
        # ⚠️ gemini-2.5-* is still ON THE ROSTER and 404s for new keys.
        prefer=("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3"),
    ),
    "openrouter": Provider(
        key="openrouter",
        base_url="https://openrouter.ai/api/v1",
        secret_name="OPENROUTER_API_TOKEN",
        prefer=(":free",),
        extra_headers={"HTTP-Referer": "https://github.com/ciresnave/OverMind",
                       "X-Title": "OverMind"},
    ),
    "nvidia": Provider(
        key="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        secret_name="NVIDIA_NIM_API_TOKEN",
        # ⚠️ Measured entitled models. 46 of 80 rostered ones 404 for this
        # account, so preference alone is not enough - these are known-served.
        prefer=("gpt-oss", "nemotron-3.5-lightning", "kimi"),
        fallback_models=("openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b",
                         "moonshotai/kimi-k3"),
    ),
    "cloudflare": Provider(
        key="cloudflare",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
        secret_name="CLOUDFLARE_WORKERS_AI_API_TOKEN",
        account_secret_name="CLOUDFLARE_ACCOUNT_ID",
        strict_message_schema=True,
        models_path=None,               # measured: GET /ai/v1/models -> 405
        fallback_models=("@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                         "@cf/openai/gpt-oss-120b"),
    ),
    "ollama": Provider(
        key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        secret_name="OLLAMA_API_KEY",   # ignored by Ollama; kept for shape
        prefer=("qwen3", "llama3.2"),
        fallback_models=("qwen3:8b", "llama3.2:3b"),
    ),
}


# --------------------------------------------------------------------------- #
# Model selection
# --------------------------------------------------------------------------- #

#: ⚠️ NOT EVERY ID ON A ROSTER IS A CHAT MODEL. A guardrail, embedder or
#: reranker whose name contains "llama-3.1" is selected by a naive substring
#: match and then answers "tool use is unsupported" - which reads as a PROVIDER
#: verdict and is a selection bug. Measured on NVIDIA.
NON_CHAT = ("guard", "safety", "embed", "rerank", "reward", "ocr", "moderation",
            "whisper", "tts", "stt", "diffusion", "image", "video", "retriever",
            "classifier", "parse", "nemoretriever", "nvclip")


def select_models(roster: Iterable[str], prefer: Sequence[str],
                  limit: int = 4) -> list[str]:
    """Ordered candidates. ⚠️ A LIST, never a single guess - see the roster note."""
    ids = [m for m in roster if m and not any(b in m.lower() for b in NON_CHAT)]
    picked: list[str] = []
    for token in prefer:
        for m in ids:
            if token.lower() in m.lower() and m not in picked:
                picked.append(m)
                if len(picked) >= limit:
                    return picked
    for m in ids:
        if m not in picked:
            picked.append(m)
            if len(picked) >= limit:
                break
    return picked


def normalise_for_echo(message: Mapping[str, Any]) -> dict[str, Any]:
    """Make an assistant message safe to send back.

    ⚠️ A tool-call message carries `content: null`. Four providers tolerate it;
    Cloudflare rejects it with "oneOf at '/' not met … Type mismatch of
    '/messages/0/content'", which names a schema path rather than the cause.
    """
    out: dict[str, Any] = {"role": message.get("role", "assistant"),
                           "content": message.get("content") or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = message["tool_calls"]
    return out


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #

@dataclass
class Usage:
    """Tokens a call consumed, as the PROVIDER reports them.

    ⚠️ Read from the response, never estimated from the text. Every provider
    measured returns an OpenAI-shaped `usage` block; a provider that does not is
    recorded as UNKNOWN rather than as zero, because zero is a number and
    "it did not say" is not.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reported: bool = False

    @classmethod
    def zero(cls) -> "Usage":
        """The identity for summing. ⚠️ `reported=True` deliberately: it means
        "everything summed so far WAS reported", which is vacuously true of
        nothing. The default `Usage()` has `reported=False` - meaning "a call
        happened and said nothing" - and using that as an accumulator poisoned
        every total to INCOMPLETE, including totals where every call had in fact
        reported. Two different meanings for an all-zero value, and the wrong
        one was the default.
        """
        return cls(reported=True)

    @classmethod
    def from_response(cls, body: Mapping[str, Any]) -> "Usage":
        raw = body.get("usage")
        if not isinstance(raw, Mapping):
            return cls(reported=False)
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        return cls(prompt_tokens=prompt, completion_tokens=completion,
                   total_tokens=int(raw.get("total_tokens") or (prompt + completion)),
                   reported=True)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.prompt_tokens + other.prompt_tokens,
                     self.completion_tokens + other.completion_tokens,
                     self.total_tokens + other.total_tokens,
                     # ⚠️ A sum is only fully reported if EVERY part was. One
                     # silent provider makes the total a floor, not a figure.
                     self.reported and other.reported)

    def __str__(self) -> str:
        if not self.reported:
            return f"{self.total_tokens} tokens (INCOMPLETE - a call reported none)"
        return (f"{self.total_tokens} tokens "
                f"(in {self.prompt_tokens}, out {self.completion_tokens})")


@dataclass
class ChatResult:
    message: dict[str, Any]
    model: str
    provider: str
    latency_s: float
    usage: Usage = field(default_factory=Usage)
    #: Why generation stopped, as the provider reports it. ⚠️ "length" means the
    #: reply was CUT OFF, which is a different fact from having nothing to say.
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """⚠️ MEASURED THREE TIMES, on three different models, each time read as
        model incapacity: an output budget too small for a THINKING model is
        spent reasoning, and the reply arrives empty.

        gemini-3.6-flash returned 2-8 visible characters at max_tokens=160 and
        every rule scored VOID (§13). qwen3:8b at max_tokens=600 executed
        nothing and scored SILENT; at 2000, same schemas and same task, it
        completed. A budget problem and a refusal are indistinguishable in the
        content - and only `finish_reason` tells them apart.
        """
        return (self.finish_reason or "").lower() in ("length", "max_tokens", "max_output_tokens")

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return list(self.message.get("tool_calls") or [])

    @property
    def content(self) -> str:
        return self.message.get("content") or ""


class ProviderClient:
    """One OpenAI-shaped client. `provider` is the only thing that changes."""

    def __init__(self, provider: str | Provider, *, timeout: float = 90.0,
                 max_tokens: int = 512, model: str | None = None,
                 opener: Any | None = None) -> None:
        self.provider = PROVIDERS[provider] if isinstance(provider, str) else provider
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.pinned_model = model
        self._roster: list[str] | None = None
        self._known_bad: set[str] = set()
        # injectable purely so tests need no network
        self._open = opener or self._urlopen

    # -- transport ---------------------------------------------------------- #

    def _urlopen(self, url: str, payload: Mapping[str, Any] | None,
                 headers: Mapping[str, str], timeout: float):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=dict(headers),
                                     method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")

    def _headers(self) -> dict[str, str]:
        token = read_secret(self.provider.secret_name) or "unused"
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            # ⚠️ Without a real User-Agent, Groq's edge answers 403 "error code:
            # 1010" - a bot block that reads exactly like a rejected key.
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {token}",
            **self.provider.extra_headers,
        }

    def _request(self, path: str, payload: Mapping[str, Any] | None = None,
                 timeout: float | None = None) -> dict[str, Any]:
        url = self.provider.resolved_base() + path
        try:
            _, body = self._open(url, payload, self._headers(), timeout or self.timeout)
            return body
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                raise RateLimited(self.provider.key, 429, text) from None
            raise ProviderError(self.provider.key, exc.code, text) from None
        except urllib.error.URLError as exc:
            raise ProviderError(self.provider.key, None, str(exc.reason)) from None

    # -- models ------------------------------------------------------------- #

    def roster(self) -> list[str]:
        """Model ids the provider ADVERTISES. ⚠️ Not what it will SERVE you."""
        if self._roster is not None:
            return self._roster
        if self.provider.models_path is None:
            self._roster = list(self.provider.fallback_models)
            return self._roster
        try:
            body = self._request(self.provider.models_path)
            data = body.get("data") or body.get("models") or []
            ids = [d.get("id") or d.get("name") for d in data if isinstance(d, dict)]
            self._roster = [i for i in ids if i] or list(self.provider.fallback_models)
        except ProviderError:
            self._roster = list(self.provider.fallback_models)
        return self._roster

    def candidates(self, limit: int = 4) -> list[str]:
        if self.pinned_model:
            return [self.pinned_model]
        picked = select_models(self.roster(), self.provider.prefer, limit=limit)
        for extra in self.provider.fallback_models:
            if extra not in picked:
                picked.append(extra)
        return [m for m in picked if m not in self._known_bad][:limit + 2]

    # -- chat --------------------------------------------------------------- #

    def chat(self, messages: Sequence[Mapping[str, Any]], *,
             tools: Sequence[Mapping[str, Any]] | None = None,
             max_tokens: int | None = None, temperature: float = 0.0,
             retries_on_429: int = 2) -> ChatResult:
        """One completion, failing over across candidate models.

        ⚠️ FAILING OVER IS NOT OPTIONAL. Free availability is per-model and
        transient, and a rostered model may simply not be served to this account.
        """
        # ⚠️ RESOLVE CONFIGURATION BEFORE THE FAILOVER LOOP. A missing account id
        # is a CONFIGURATION error, and no number of alternative models fixes it.
        # Raised inside the loop it was caught per-model and reported as
        # "no usable model … HTTP None" - a specific, actionable cause converted
        # into a generic one by the handler meant to be robust. Same shape as
        # every instrument failure in MEASUREMENTS.md §14: the honest answer
        # replaced by a comfortable one.
        self.provider.resolved_base()

        payload_messages = [normalise_for_echo(m) if m.get("role") == "assistant" else dict(m)
                            for m in messages]
        attempts: list[tuple[str, str]] = []
        for model in self.candidates():
            payload: dict[str, Any] = {
                "model": model,
                "messages": payload_messages,
                "temperature": temperature,
                # ⚠️ A thinking model spends this before it answers; too small a
                # budget looks exactly like a model that will not comply.
                "max_tokens": max_tokens or self.max_tokens,
            }
            if tools:
                payload["tools"] = list(tools)

            for attempt in range(retries_on_429 + 1):
                started = time.time()
                try:
                    body = self._request("/chat/completions", payload)
                except RateLimited as exc:
                    if attempt < retries_on_429:
                        time.sleep(2 ** attempt)
                        continue
                    attempts.append((model, "rate-limited"))
                    break
                except ProviderError as exc:
                    # 404 here means "not served to this account" far more often
                    # than "no such model" - either way, try the next candidate.
                    self._known_bad.add(model)
                    attempts.append((model, f"HTTP {exc.status}"))
                    break
                choices = body.get("choices") or []
                if not choices:
                    attempts.append((model, "no choices returned"))
                    break
                return ChatResult(
                    message=dict(choices[0].get("message") or {}),
                    model=model, provider=self.provider.key,
                    latency_s=time.time() - started,
                    usage=Usage.from_response(body),
                    finish_reason=choices[0].get("finish_reason"),
                    raw=body,
                )
        raise NoUsableModel(self.provider.key, attempts)


def available_providers() -> list[str]:
    """Providers whose secret is actually readable right now."""
    out = []
    for name, prov in PROVIDERS.items():
        if name == "ollama" or read_secret(prov.secret_name):
            if prov.account_secret_name and not read_secret(prov.account_secret_name):
                continue
            out.append(name)
    return out
