"""LLM provider switch. anthropic/openai go through LangChain chat models; claude_subscription uses the
local `claude` CLI login via claude-agent-sdk (local demo only). No silent fallback between providers."""
import os
import shutil
from pathlib import Path

PROVIDERS = ("anthropic", "openai", "claude_subscription")
_current: str | None = None  # runtime override of AI_PROVIDER


class NotConfigured(Exception):
    pass


class UpstreamError(Exception):
    pass


def _bundled_cli() -> bool:
    try:
        import claude_agent_sdk
        return (Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude").exists()
    except Exception:
        return False


def get_provider() -> str:
    p = _current or os.environ.get("AI_PROVIDER", "anthropic")
    return p if p in PROVIDERS else "anthropic"


def set_provider(p: str):
    global _current
    if p not in PROVIDERS:
        raise ValueError(p)
    _current = p


def model_name(p: str) -> str:
    if p == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    if p == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o")
    return os.environ.get("CLAUDE_SUBSCRIPTION_MODEL") or "claude (subscription default)"


def missing(p: str) -> str | None:
    """Human-readable reason the provider can't be used, or None if configured."""
    if p == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY is not set. Add it to .env and restart the server."
    if p == "openai" and not os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set. Add it to .env and restart the server."
    if p == "claude_subscription":
        if os.environ.get("RUNNING_IN_DOCKER") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return ("Inside Docker the subscription needs CLAUDE_CODE_OAUTH_TOKEN (run `claude setup-token` on your "
                    "machine, put the token in .env, then `docker compose up -d`). A Mac Keychain login can't be shared with the container.")
        if not shutil.which("claude") and not _bundled_cli():
            return "The `claude` CLI was not found. Install Claude Code and run `claude login`."
    return None


def env_warning() -> str | None:
    v = os.environ.get("AI_PROVIDER")
    if v and v not in PROVIDERS:
        return f"AI_PROVIDER is set to '{v}', which is not a provider, so 'anthropic' is used. Set it to one of: {', '.join(PROVIDERS)}."
    return None


def status() -> dict:
    return {"current": get_provider(), "warning": env_warning(), "providers": {
        p: {"configured": missing(p) is None, "model": model_name(p), "issue": missing(p)} for p in PROVIDERS}}


def chat_model(p: str):
    """LangChain chat model for an API-key provider."""
    if (why := missing(p)):
        raise NotConfigured(f"Provider '{p}' is not configured: {why}")
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name(p), max_tokens=4096, timeout=90)
    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name(p), timeout=90)
    raise ValueError(f"{p} is not an API-key provider")
