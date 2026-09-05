"""hash-cli configuration — model selection and API key storage.

Config is stored at ~/.hash-cli/config.json (plain JSON, keys obfuscated
with base64 so they are not sitting in cleartext but this is NOT encryption —
treat the file as sensitive and don't commit it).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR  = Path.home() / ".hash-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"

# ── Supported models ─────────────────────────────────────────────────────

MODELS: list[dict] = [
    # ── Free (Ollama — local) ─────────────────────────────────────────
    {
        "id":        "ollama/llama3.1:8b",
        "label":     "Llama 3.1 8B          (free)  — solid all-rounder",
        "provider":  "ollama",
        "model":     "llama3.1:8b",
        "needs_key": False,
        "pull_cmd":  "ollama pull llama3.1:8b",
    },
    {
        "id":        "ollama/llama3.2:3b",
        "label":     "Llama 3.2 3B          (free)  — fastest local model",
        "provider":  "ollama",
        "model":     "llama3.2:3b",
        "needs_key": False,
        "pull_cmd":  "ollama pull llama3.2:3b",
    },
    {
        "id":        "ollama/qwen2.5-coder:7b",
        "label":     "Qwen 2.5 Coder 7B     (free)  — best local code model",
        "provider":  "ollama",
        "model":     "qwen2.5-coder:7b",
        "needs_key": False,
        "pull_cmd":  "ollama pull qwen2.5-coder:7b",
    },
    {
        "id":        "ollama/llama4:scout",
        "label":     "Llama 4 Scout 17B     (free)  — needs 16GB+ RAM, may crash on small machines",
        "provider":  "ollama",
        "model":     "llama4:17b-scout-16e-instruct-q4_K_M",
        "needs_key": False,
        "pull_cmd":  "ollama pull llama4:17b-scout-16e-instruct-q4_K_M",
    },
    {
        "id":        "ollama/deepseek-r1:8b",
        "label":     "DeepSeek R1 8B        (free)  — strong reasoning",
        "provider":  "ollama",
        "model":     "deepseek-r1:8b",
        "needs_key": False,
        "pull_cmd":  "ollama pull deepseek-r1:8b",
    },
    # ── Premium (API key required) ────────────────────────────────────
    {
        "id":        "openai/gpt-5.6-luna",
        "label":     "GPT-5.6 Luna          (premium) — OpenAI, economy tier, very cheap & fast",
        "provider":  "openai",
        "model":     "gpt-5.6-luna",
        "needs_key": True,
        "key_env":   "OPENAI_API_KEY",
        "key_url":   "https://platform.openai.com/api-keys",
    },
    {
        "id":        "openai/gpt-5.6-terra",
        "label":     "GPT-5.6 Terra         (premium) — OpenAI, balanced everyday model",
        "provider":  "openai",
        "model":     "gpt-5.6-terra",
        "needs_key": True,
        "key_env":   "OPENAI_API_KEY",
        "key_url":   "https://platform.openai.com/api-keys",
    },
    {
        "id":        "openai/gpt-5.6-sol",
        "label":     "GPT-5.6 Sol           (premium) — OpenAI, flagship, best reasoning",
        "provider":  "openai",
        "model":     "gpt-5.6-sol",
        "needs_key": True,
        "key_env":   "OPENAI_API_KEY",
        "key_url":   "https://platform.openai.com/api-keys",
    },
    {
        "id":        "anthropic/claude-3-5-haiku-20241022",
        "label":     "Claude Haiku 4.5      (premium) — Anthropic, fast & cheap",
        "provider":  "anthropic",
        "model":     "claude-haiku-4-5",
        "needs_key": True,
        "key_env":   "ANTHROPIC_API_KEY",
        "key_url":   "https://console.anthropic.com/settings/keys",
    },
    {
        "id":        "anthropic/claude-sonnet-4-6",
        "label":     "Claude Sonnet 4.6     (premium) — Anthropic, balanced, 1M context",
        "provider":  "anthropic",
        "model":     "claude-sonnet-4-6",
        "needs_key": True,
        "key_env":   "ANTHROPIC_API_KEY",
        "key_url":   "https://console.anthropic.com/settings/keys",
    },
    {
        "id":        "anthropic/claude-opus-4-8",
        "label":     "Claude Opus 4.8       (premium) — Anthropic, most intelligent",
        "provider":  "anthropic",
        "model":     "claude-opus-4-8",
        "needs_key": True,
        "key_env":   "ANTHROPIC_API_KEY",
        "key_url":   "https://console.anthropic.com/settings/keys",
    },
    {
        "id":        "google/gemini-3.6-flash",
        "label":     "Gemini 3.6 Flash      (premium) — Google, latest, FREE tier available",
        "provider":  "google",
        "model":     "gemini-3.6-flash",
        "needs_key": True,
        "key_env":   "GOOGLE_API_KEY",
        "key_url":   "https://aistudio.google.com/app/apikey",
    },
    {
        "id":        "google/gemini-2.5-flash",
        "label":     "Gemini 2.5 Flash      (premium) — Google, FREE tier available",
        "provider":  "google",
        "model":     "gemini-2.5-flash",
        "needs_key": True,
        "key_env":   "GOOGLE_API_KEY",
        "key_url":   "https://aistudio.google.com/app/apikey",
    },
    {
        "id":        "deepseek/deepseek-v4-flash",
        "label":     "DeepSeek V4 Flash     (premium) — DeepSeek API, fast & very cheap",
        "provider":  "openai",
        "model":     "deepseek-v4-flash",
        "needs_key": True,
        "key_env":   "DEEPSEEK_API_KEY",
        "key_url":   "https://platform.deepseek.com/api_keys",
        "base_url":  "https://api.deepseek.com/v1",
    },
    {
        "id":        "deepseek/deepseek-v4-pro",
        "label":     "DeepSeek V4 Pro       (premium) — DeepSeek API, flagship 1.6T MoE",
        "provider":  "openai",
        "model":     "deepseek-v4-pro",
        "needs_key": True,
        "key_env":   "DEEPSEEK_API_KEY",
        "key_url":   "https://platform.deepseek.com/api_keys",
        "base_url":  "https://api.deepseek.com/v1",
    },
]


# ── Config I/O ────────────────────────────────────────────────────────────

def _load() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    CONFIG_FILE.chmod(0o600)  # owner read/write only


def _encode(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _decode(value: str) -> str:
    try:
        return base64.b64decode(value.encode()).decode()
    except Exception:
        return value


# ── Public API ────────────────────────────────────────────────────────────

def get_active_model_id() -> str:
    return _load().get("active_model", "ollama/llama3.1:8b")


def set_active_model(model_id: str) -> None:
    data = _load()
    data["active_model"] = model_id
    _save(data)


def save_api_key(provider: str, key: str, key_env: str | None = None) -> None:
    data = _load()
    data.setdefault("keys", {})[provider] = _encode(key)
    if key_env:
        data.setdefault("key_envs", {})[provider] = key_env
    _save(data)
    if key_env:
        os.environ[key_env] = key


# ── Per-env-var key storage (the reliable approach) ───────────────────────
# Keys are stored keyed by their environment variable name, so OpenAI
# (OPENAI_API_KEY) and DeepSeek (DEEPSEEK_API_KEY) never collide even
# though both use provider="openai".

def save_api_key_for_env(key_env: str, key: str) -> None:
    """Store an API key under its environment variable name."""
    data = _load()
    data.setdefault("env_keys", {})[key_env] = _encode(key)
    _save(data)
    os.environ[key_env] = key


def get_api_key_for_env(key_env: str) -> str | None:
    """Return the stored API key for a given env var name."""
    if not key_env:
        return None
    raw = _load().get("env_keys", {}).get(key_env)
    if raw:
        return _decode(raw)
    return os.environ.get(key_env)


def remove_api_key_for_env(key_env: str) -> bool:
    """Remove a stored key. Returns True if one existed."""
    data = _load()
    env_keys = data.get("env_keys", {})
    if key_env in env_keys:
        del env_keys[key_env]
        data["env_keys"] = env_keys
        _save(data)
        os.environ.pop(key_env, None)
        return True
    return False


def list_stored_keys() -> dict[str, str]:
    """Return {env_var: masked_key} for all stored keys."""
    data = _load()
    result = {}
    for env_name, encoded in data.get("env_keys", {}).items():
        k = _decode(encoded)
        masked = k[:7] + "…" + k[-4:] if len(k) > 14 else "***"
        result[env_name] = masked
    return result


def get_api_key(provider: str) -> str | None:
    raw = _load().get("keys", {}).get(provider)
    if raw:
        return _decode(raw)
    # Also check environment variables
    model_info = next((m for m in MODELS if m["provider"] == provider), None)
    if model_info and model_info.get("key_env"):
        return os.environ.get(model_info["key_env"])
    return None


# ── Custom user-added models ──────────────────────────────────────────────

def get_custom_models() -> list[dict]:
    """Return the list of user-added custom models from config."""
    return _load().get("custom_models", [])


def get_all_models() -> list[dict]:
    """Return built-in models plus any custom user-added models."""
    return list(MODELS) + get_custom_models()


def add_custom_model(model: dict) -> tuple[bool, str]:
    """Add a custom model definition. Returns (success, message)."""
    required = {"id", "label", "provider", "model", "needs_key"}
    missing = required - set(model.keys())
    if missing:
        return False, f"Missing fields: {', '.join(missing)}"

    data = _load()
    customs = data.setdefault("custom_models", [])
    # Replace if an entry with the same id already exists
    customs[:] = [m for m in customs if m.get("id") != model["id"]]
    customs.append(model)
    _save(data)
    return True, f"Added custom model '{model['model']}'."


def remove_custom_model(model_id: str) -> bool:
    data = _load()
    customs = data.get("custom_models", [])
    new = [m for m in customs if m.get("id") != model_id]
    if len(new) == len(customs):
        return False
    data["custom_models"] = new
    _save(data)
    return True


def get_model_info(model_id: str) -> dict | None:
    return next((m for m in get_all_models() if m["id"] == model_id), None)


def get_active_model_info() -> dict:
    mid = get_active_model_id()
    info = get_model_info(mid)
    return info if info else MODELS[0]


def apply_api_keys_to_env() -> None:
    """Load all stored API keys into environment variables.

    Uses direct assignment (not setdefault) so fresh keys overwrite stale ones.
    """
    data = _load()

    # New per-env-var storage (preferred)
    for env_name, encoded in data.get("env_keys", {}).items():
        os.environ[env_name] = _decode(encoded)

    # Legacy per-provider storage (backward compat)
    key_envs = data.get("key_envs", {})
    for provider, encoded in data.get("keys", {}).items():
        key = _decode(encoded)
        env_name = key_envs.get(provider)
        if not env_name:
            model_info = next((m for m in MODELS if m["provider"] == provider), None)
            env_name = model_info.get("key_env") if model_info else None
        if env_name and env_name not in data.get("env_keys", {}):
            os.environ[env_name] = key
