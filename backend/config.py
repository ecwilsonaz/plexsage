"""Configuration loading with environment variable priority."""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from backend.models import AppConfig, DefaultsConfig, LLMConfig, PlexConfig

# Load .env file (if it exists) - env vars take priority
load_dotenv()

# User config file path (for UI-saved settings)
USER_CONFIG_PATH = Path("config.user.yaml")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def remove_empty_values(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys with empty string or None values, recursively."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            nested = remove_empty_values(v)
            if nested:  # Only include non-empty dicts
                result[k] = nested
        elif v not in (None, ""):
            result[k] = v
    return result


# Default model mappings per provider
MODEL_DEFAULTS = {
    "anthropic": {
        "analysis": "claude-sonnet-4-5",
        "generation": "claude-haiku-4-5",
    },
    "openai": {
        "analysis": "gpt-4.1",
        "generation": "gpt-4.1-mini",
    },
    "gemini": {
        "analysis": "gemini-2.5-flash",
        "generation": "gemini-2.5-flash",
    },
    "ollama": {
        "analysis": "",  # Populated from Ollama API
        "generation": "",
    },
    "custom": {
        "analysis": "",  # User-specified
        "generation": "",
    },
}


def load_yaml_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = Path("config.yaml")

    if not config_path.exists():
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_user_yaml_config() -> dict[str, Any]:
    """Load user configuration from config.user.yaml."""
    if not USER_CONFIG_PATH.exists():
        return {}
    with open(USER_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def save_user_config(updates: dict[str, Any]) -> None:
    """Save user configuration to config.user.yaml.

    Only saves non-empty values. Preserves existing user config.
    """
    existing = load_user_yaml_config()
    merged = deep_merge(existing, updates)
    cleaned = remove_empty_values(merged)

    with open(USER_CONFIG_PATH, "w") as f:
        yaml.dump(cleaned, f, default_flow_style=False)


def get_env_or_yaml(
    env_key: str, yaml_value: Any, default: Any = None
) -> Any:
    """Get value from environment variable or fall back to YAML value."""
    env_value = os.environ.get(env_key)
    if env_value is not None:
        return env_value
    if yaml_value is not None:
        return yaml_value
    return default


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration with priority chain.

    Priority order:
    1. Environment variables (highest)
    2. config.user.yaml (UI-saved settings)
    3. config.yaml file
    4. Default values (lowest)
    """
    yaml_config = load_yaml_config(config_path)
    user_config = load_user_yaml_config()

    # Merge: user config overrides base yaml config
    yaml_config = deep_merge(yaml_config, user_config)

    # Extract nested config sections
    plex_yaml = yaml_config.get("plex", {})
    llm_yaml = yaml_config.get("llm", {})
    defaults_yaml = yaml_config.get("defaults", {})

    # Determine LLM provider - explicit setting or auto-detect from API keys
    explicit_provider = get_env_or_yaml(
        "LLM_PROVIDER", llm_yaml.get("provider"), None
    )

    # Check which API keys are available
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or llm_yaml.get("api_key", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    # Auto-detect provider if not explicitly set
    if explicit_provider:
        provider = explicit_provider
    elif gemini_key:
        provider = "gemini"
    elif openai_key:
        provider = "openai"
    elif anthropic_key:
        provider = "anthropic"
    else:
        provider = "gemini"  # Default

    # Get API key based on provider
    if provider == "anthropic":
        api_key = anthropic_key
    elif provider == "openai":
        api_key = openai_key
    elif provider == "gemini":
        api_key = gemini_key
    else:
        api_key = llm_yaml.get("api_key", "")

    # Get model defaults for the provider
    provider_defaults = MODEL_DEFAULTS.get(provider, MODEL_DEFAULTS["gemini"])

    # Build configuration
    plex_config = PlexConfig(
        url=get_env_or_yaml("PLEX_URL", plex_yaml.get("url"), ""),
        token=get_env_or_yaml("PLEX_TOKEN", plex_yaml.get("token"), ""),
        music_library=get_env_or_yaml(
            "PLEX_MUSIC_LIBRARY", plex_yaml.get("music_library"), "Music"
        ),
    )

    # Get local provider settings
    ollama_url = get_env_or_yaml(
        "OLLAMA_URL", llm_yaml.get("ollama_url"), "http://localhost:11434"
    )
    ollama_context_window_str = get_env_or_yaml(
        "OLLAMA_CONTEXT_WINDOW", llm_yaml.get("ollama_context_window"), 32768
    )
    ollama_context_window = int(ollama_context_window_str) if isinstance(
        ollama_context_window_str, str
    ) else ollama_context_window_str
    custom_url = get_env_or_yaml(
        "CUSTOM_LLM_URL", llm_yaml.get("custom_url"), ""
    )
    custom_context_window_str = get_env_or_yaml(
        "CUSTOM_CONTEXT_WINDOW", llm_yaml.get("custom_context_window"), 32768
    )
    # Handle string from env var
    custom_context_window = int(custom_context_window_str) if isinstance(
        custom_context_window_str, str
    ) else custom_context_window_str

    # Determine model names with proper fallback chain
    # When env var overrides to a DIFFERENT provider, use that provider's defaults
    # (prevents using custom provider's model names with gemini provider, etc.)
    env_provider = os.environ.get("LLM_PROVIDER")
    yaml_provider = llm_yaml.get("provider")
    provider_changed_by_env = env_provider and env_provider != yaml_provider

    if provider_changed_by_env:
        # Env var switched to different provider - use new provider's defaults
        # (unless model env vars are also explicitly set)
        model_analysis = os.environ.get("LLM_MODEL_ANALYSIS") or provider_defaults["analysis"]
        model_generation = os.environ.get("LLM_MODEL_GENERATION") or provider_defaults["generation"]
    else:
        # Same provider or no env override - YAML models take precedence
        model_analysis = get_env_or_yaml(
            "LLM_MODEL_ANALYSIS",
            llm_yaml.get("model_analysis"),
            provider_defaults["analysis"],
        )
        model_generation = get_env_or_yaml(
            "LLM_MODEL_GENERATION",
            llm_yaml.get("model_generation"),
            provider_defaults["generation"],
        )

    llm_config = LLMConfig(
        provider=provider,
        api_key=api_key,
        model_analysis=model_analysis,
        model_generation=model_generation,
        smart_generation=llm_yaml.get("smart_generation", False),
        ollama_url=ollama_url,
        ollama_context_window=ollama_context_window,
        custom_url=custom_url,
        custom_context_window=custom_context_window,
    )

    defaults_config = DefaultsConfig(
        track_count=defaults_yaml.get("track_count", 25)
    )

    return AppConfig(
        plex=plex_config,
        llm=llm_config,
        defaults=defaults_config,
    )


# Global config instance (loaded on import, can be refreshed)
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the current configuration, loading if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def refresh_config(config_path: Path | None = None) -> AppConfig:
    """Reload configuration from file and environment."""
    global _config
    _config = load_config(config_path)
    return _config


def update_config_values(updates: dict[str, Any]) -> AppConfig:
    """Update configuration values and persist to config.user.yaml.

    Changes are saved to config.user.yaml so they survive server restarts.
    Environment variables still take priority over saved settings.
    """
    global _config
    if _config is None:
        _config = load_config()

    # Create updated config by merging updates
    plex_updates = {}
    llm_updates = {}

    if "plex_url" in updates and updates["plex_url"]:
        plex_updates["url"] = updates["plex_url"]
    if "plex_token" in updates and updates["plex_token"]:
        plex_updates["token"] = updates["plex_token"]
    if "music_library" in updates and updates["music_library"]:
        plex_updates["music_library"] = updates["music_library"]

    if "llm_provider" in updates and updates["llm_provider"]:
        new_provider = updates["llm_provider"]
        llm_updates["provider"] = new_provider

        # Auto-select API key from environment if provider changed and no key provided
        if not updates.get("llm_api_key"):
            env_keys = {
                "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
                "openai": os.environ.get("OPENAI_API_KEY", ""),
                "gemini": os.environ.get("GEMINI_API_KEY", ""),
            }
            if env_keys.get(new_provider):
                llm_updates["api_key"] = env_keys[new_provider]

        # Auto-select default models for new provider
        if new_provider in MODEL_DEFAULTS:
            defaults = MODEL_DEFAULTS[new_provider]
            if not updates.get("model_analysis"):
                llm_updates["model_analysis"] = defaults["analysis"]
            if not updates.get("model_generation"):
                llm_updates["model_generation"] = defaults["generation"]

    if "llm_api_key" in updates and updates["llm_api_key"]:
        llm_updates["api_key"] = updates["llm_api_key"]
    if "model_analysis" in updates and updates["model_analysis"]:
        llm_updates["model_analysis"] = updates["model_analysis"]
    if "model_generation" in updates and updates["model_generation"]:
        llm_updates["model_generation"] = updates["model_generation"]

    # Local provider settings
    if "ollama_url" in updates and updates["ollama_url"]:
        llm_updates["ollama_url"] = updates["ollama_url"]
    if "ollama_context_window" in updates and updates["ollama_context_window"]:
        llm_updates["ollama_context_window"] = updates["ollama_context_window"]
    if "custom_url" in updates and updates["custom_url"]:
        llm_updates["custom_url"] = updates["custom_url"]
    if "custom_context_window" in updates and updates["custom_context_window"]:
        llm_updates["custom_context_window"] = updates["custom_context_window"]

    # Create new config with updates
    new_plex = _config.plex.model_copy(update=plex_updates)
    new_llm = _config.llm.model_copy(update=llm_updates)

    _config = AppConfig(
        plex=new_plex,
        llm=new_llm,
        defaults=_config.defaults,
    )

    # Persist to user config file
    user_updates: dict[str, Any] = {}
    if plex_updates:
        user_updates["plex"] = plex_updates
    if llm_updates:
        user_updates["llm"] = llm_updates

    if user_updates:
        save_user_config(user_updates)

    return _config
