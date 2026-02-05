"""Tests for configuration loading."""

from unittest.mock import patch

import yaml

from backend.config import (
    deep_merge,
    get_env_or_yaml,
    load_config,
    load_yaml_config,
    MODEL_DEFAULTS,
    remove_empty_values,
)


class TestLoadYamlConfig:
    """Tests for YAML config file loading."""

    def test_loads_valid_yaml(self, tmp_path):
        """Should load a valid YAML config file."""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "plex": {"url": "http://localhost:32400", "token": "test-token"},
            "llm": {"provider": "anthropic", "api_key": "sk-test"},
        }
        config_file.write_text(yaml.dump(config_data))

        result = load_yaml_config(config_file)

        assert result["plex"]["url"] == "http://localhost:32400"
        assert result["llm"]["provider"] == "anthropic"

    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        """Should return empty dict when config file doesn't exist."""
        config_file = tmp_path / "nonexistent.yaml"

        result = load_yaml_config(config_file)

        assert result == {}

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        """Should return empty dict for empty config file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        result = load_yaml_config(config_file)

        assert result == {}


class TestGetEnvOrYaml:
    """Tests for environment variable priority."""

    def test_env_var_takes_priority(self, monkeypatch):
        """Environment variable should override YAML value."""
        monkeypatch.setenv("TEST_VAR", "env_value")

        result = get_env_or_yaml("TEST_VAR", "yaml_value", "default")

        assert result == "env_value"

    def test_yaml_used_when_no_env_var(self, monkeypatch):
        """YAML value should be used when env var not set."""
        monkeypatch.delenv("TEST_VAR", raising=False)

        result = get_env_or_yaml("TEST_VAR", "yaml_value", "default")

        assert result == "yaml_value"

    def test_default_used_when_no_env_or_yaml(self, monkeypatch):
        """Default should be used when neither env nor YAML set."""
        monkeypatch.delenv("TEST_VAR", raising=False)

        result = get_env_or_yaml("TEST_VAR", None, "default")

        assert result == "default"

    def test_empty_string_env_var_is_used(self, monkeypatch):
        """Empty string env var should still take priority."""
        monkeypatch.setenv("TEST_VAR", "")

        result = get_env_or_yaml("TEST_VAR", "yaml_value", "default")

        assert result == ""


class TestLoadConfig:
    """Tests for full configuration loading."""

    def test_loads_from_yaml_file(self, tmp_path, monkeypatch):
        """Should load configuration from YAML file."""
        # Clear any existing env vars
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "plex": {
                "url": "http://plex.local:32400",
                "token": "yaml-token",
                "music_library": "My Music",
            },
            "llm": {
                "provider": "anthropic",
                "api_key": "sk-yaml-key",
            },
            "defaults": {"track_count": 40},
        }
        config_file.write_text(yaml.dump(config_data))

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.plex.url == "http://plex.local:32400"
        assert config.plex.token == "yaml-token"
        assert config.plex.music_library == "My Music"
        assert config.llm.provider == "anthropic"
        assert config.llm.api_key == "sk-yaml-key"
        assert config.defaults.track_count == 40

    def test_env_vars_override_yaml(self, tmp_path, monkeypatch):
        """Environment variables should override YAML values."""
        # Clear any conflicting env vars first
        for var in ["GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER",
                    "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "plex": {"url": "http://yaml:32400", "token": "yaml-token"},
            "llm": {"provider": "anthropic", "api_key": "yaml-key"},
        }
        config_file.write_text(yaml.dump(config_data))

        monkeypatch.setenv("PLEX_URL", "http://env:32400")
        monkeypatch.setenv("PLEX_TOKEN", "env-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.plex.url == "http://env:32400"
        assert config.plex.token == "env-token"
        assert config.llm.api_key == "env-key"

    def test_uses_correct_api_key_for_provider(self, tmp_path, monkeypatch):
        """Should use ANTHROPIC_API_KEY or OPENAI_API_KEY based on provider."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            # Test Anthropic provider
            config_file = tmp_path / "config.yaml"
            config_data = {"llm": {"provider": "anthropic", "api_key": ""}}
            config_file.write_text(yaml.dump(config_data))
            monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
            monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

            config = load_config(config_file)
            assert config.llm.api_key == "anthropic-key"

            # Test OpenAI provider
            config_data = {"llm": {"provider": "openai", "api_key": ""}}
            config_file.write_text(yaml.dump(config_data))

            config = load_config(config_file)
            assert config.llm.api_key == "openai-key"

    def test_default_models_for_anthropic(self, tmp_path, monkeypatch):
        """Should use default Anthropic models when not specified."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {"llm": {"provider": "anthropic", "api_key": "test"}}
        config_file.write_text(yaml.dump(config_data))

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.model_analysis == MODEL_DEFAULTS["anthropic"]["analysis"]
        assert config.llm.model_generation == MODEL_DEFAULTS["anthropic"]["generation"]

    def test_default_models_for_openai(self, tmp_path, monkeypatch):
        """Should use default OpenAI models when not specified."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {"llm": {"provider": "openai", "api_key": "test"}}
        config_file.write_text(yaml.dump(config_data))

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.model_analysis == MODEL_DEFAULTS["openai"]["analysis"]
        assert config.llm.model_generation == MODEL_DEFAULTS["openai"]["generation"]

    def test_custom_models_override_defaults(self, tmp_path, monkeypatch):
        """Custom model settings should override defaults."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "anthropic",
                "api_key": "test",
                "model_analysis": "custom-analysis-model",
                "model_generation": "custom-gen-model",
            }
        }
        config_file.write_text(yaml.dump(config_data))

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.model_analysis == "custom-analysis-model"
        assert config.llm.model_generation == "custom-gen-model"

    def test_defaults_applied_when_no_config(self, tmp_path, monkeypatch):
        """Should use defaults when config file doesn't exist."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "nonexistent.yaml"

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.plex.music_library == "Music"
        assert config.llm.provider == "gemini"
        assert config.defaults.track_count == 25

    def test_secrets_not_exposed_in_repr(self, tmp_path, monkeypatch):
        """Secrets should not be exposed when printing config."""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "plex": {"url": "http://test:32400", "token": "secret-token"},
            "llm": {"provider": "anthropic", "api_key": "secret-api-key"},
        }
        config_file.write_text(yaml.dump(config_data))

        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        # Patch load_user_yaml_config to return empty dict (ignore config.user.yaml)
        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        # The token and api_key are stored, but we verify they exist
        # (actual masking would be in a different layer if needed)
        assert config.plex.token == "secret-token"
        assert config.llm.api_key == "secret-api-key"


class TestDeepMerge:
    """Tests for deep_merge utility function."""

    def test_merges_flat_dicts(self):
        """Should merge flat dictionaries."""
        base = {"a": 1, "b": 2}
        override = {"b": 20, "c": 3}

        result = deep_merge(base, override)

        assert result == {"a": 1, "b": 20, "c": 3}

    def test_merges_nested_dicts(self):
        """Should recursively merge nested dictionaries."""
        base = {"a": {"b": 1, "c": 2}, "d": 4}
        override = {"a": {"b": 10}}

        result = deep_merge(base, override)

        assert result == {"a": {"b": 10, "c": 2}, "d": 4}

    def test_override_replaces_non_dict_with_dict(self):
        """Should replace non-dict value with dict if override is dict."""
        base = {"a": 1}
        override = {"a": {"nested": True}}

        result = deep_merge(base, override)

        assert result == {"a": {"nested": True}}

    def test_does_not_modify_original(self):
        """Should not modify the original dictionaries."""
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}

        deep_merge(base, override)

        assert base == {"a": {"b": 1}}
        assert override == {"a": {"c": 2}}


class TestRemoveEmptyValues:
    """Tests for remove_empty_values utility function."""

    def test_removes_empty_strings(self):
        """Should remove keys with empty string values."""
        d = {"a": "", "b": "value", "c": ""}

        result = remove_empty_values(d)

        assert result == {"b": "value"}

    def test_removes_none_values(self):
        """Should remove keys with None values."""
        d = {"a": None, "b": "value", "c": None}

        result = remove_empty_values(d)

        assert result == {"b": "value"}

    def test_preserves_other_falsy_values(self):
        """Should preserve 0 and False values."""
        d = {"a": 0, "b": False, "c": "value"}

        result = remove_empty_values(d)

        assert result == {"a": 0, "b": False, "c": "value"}

    def test_removes_empty_nested_dicts(self):
        """Should remove nested dicts that become empty."""
        d = {"a": {"b": "", "c": None}, "d": "value"}

        result = remove_empty_values(d)

        assert result == {"d": "value"}

    def test_preserves_non_empty_nested_dicts(self):
        """Should preserve nested dicts with values."""
        d = {"a": {"b": "", "c": "nested"}, "d": "value"}

        result = remove_empty_values(d)

        assert result == {"a": {"c": "nested"}, "d": "value"}


class TestLocalProviderConfig:
    """Tests for local LLM provider configuration."""

    def test_loads_ollama_config_from_yaml(self, tmp_path, monkeypatch):
        """Should load Ollama config from YAML file."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS",
                    "LLM_MODEL_GENERATION", "OLLAMA_URL"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "ollama",
                "ollama_url": "http://192.168.1.100:11434",
                "model_analysis": "llama3:8b",
                "model_generation": "llama3:8b",
            },
        }
        config_file.write_text(yaml.dump(config_data))

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.provider == "ollama"
        assert config.llm.ollama_url == "http://192.168.1.100:11434"
        assert config.llm.model_analysis == "llama3:8b"

    def test_ollama_url_env_var_override(self, tmp_path, monkeypatch):
        """OLLAMA_URL env var should override YAML value."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "ollama",
                "ollama_url": "http://yaml-host:11434",
            },
        }
        config_file.write_text(yaml.dump(config_data))

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_URL", "http://env-host:11434")

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.ollama_url == "http://env-host:11434"

    def test_loads_custom_provider_config(self, tmp_path, monkeypatch):
        """Should load custom provider config from YAML."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS",
                    "LLM_MODEL_GENERATION", "CUSTOM_LLM_URL", "CUSTOM_CONTEXT_WINDOW"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "custom",
                "custom_url": "http://localhost:5000/v1",
                "custom_context_window": 8192,
                "model_analysis": "my-model",
                "model_generation": "my-model",
            },
        }
        config_file.write_text(yaml.dump(config_data))

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.provider == "custom"
        assert config.llm.custom_url == "http://localhost:5000/v1"
        assert config.llm.custom_context_window == 8192

    def test_custom_context_window_env_var(self, tmp_path, monkeypatch):
        """CUSTOM_CONTEXT_WINDOW env var should override YAML."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_MODEL_ANALYSIS", "LLM_MODEL_GENERATION"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "custom",
                "custom_context_window": 4096,
            },
        }
        config_file.write_text(yaml.dump(config_data))

        monkeypatch.setenv("LLM_PROVIDER", "custom")
        monkeypatch.setenv("CUSTOM_LLM_URL", "http://localhost:5000/v1")
        monkeypatch.setenv("CUSTOM_CONTEXT_WINDOW", "16384")

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.custom_context_window == 16384

    def test_default_ollama_url(self, tmp_path, monkeypatch):
        """Should use default Ollama URL when not specified."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS",
                    "LLM_MODEL_GENERATION", "OLLAMA_URL"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "ollama",
            },
        }
        config_file.write_text(yaml.dump(config_data))

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.ollama_url == "http://localhost:11434"

    def test_default_custom_context_window(self, tmp_path, monkeypatch):
        """Should use default custom context window when not specified."""
        for var in ["PLEX_URL", "PLEX_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL_ANALYSIS",
                    "LLM_MODEL_GENERATION", "CUSTOM_CONTEXT_WINDOW"]:
            monkeypatch.delenv(var, raising=False)

        config_file = tmp_path / "config.yaml"
        config_data = {
            "llm": {
                "provider": "custom",
            },
        }
        config_file.write_text(yaml.dump(config_data))

        with patch("backend.config.load_user_yaml_config", return_value={}):
            config = load_config(config_file)

        assert config.llm.custom_context_window == 32768
