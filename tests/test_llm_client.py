"""Tests for LLM client."""

from unittest.mock import MagicMock, patch


class TestLLMClientInitialization:
    """Tests for LLM client initialization."""

    def test_anthropic_client_init(self, mocker):
        """Should initialize Anthropic client correctly."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="sk-ant-test-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
        )

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            client = LLMClient(config)
            mock_anthropic.Anthropic.assert_called_once_with(api_key="sk-ant-test-key")
            assert client.provider == "anthropic"

    def test_openai_client_init(self, mocker):
        """Should initialize OpenAI client correctly."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="openai",
            api_key="sk-test-key",
            model_analysis="gpt-4.1",
            model_generation="gpt-4.1-mini",
        )

        with patch("backend.llm_client.openai") as mock_openai:
            client = LLMClient(config)
            mock_openai.OpenAI.assert_called_once_with(api_key="sk-test-key")
            assert client.provider == "openai"

    def test_invalid_api_key_anthropic(self, mocker):
        """Should handle invalid Anthropic API key."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="invalid-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
        )

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = MagicMock()
            client = LLMClient(config)
            # Client should be created; validation happens on first API call
            assert client.provider == "anthropic"

    def test_invalid_api_key_openai(self, mocker):
        """Should handle invalid OpenAI API key."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="openai",
            api_key="invalid-key",
            model_analysis="gpt-4.1",
            model_generation="gpt-4.1-mini",
        )

        with patch("backend.llm_client.openai") as mock_openai:
            mock_openai.OpenAI.return_value = MagicMock()
            client = LLMClient(config)
            # Client should be created; validation happens on first API call
            assert client.provider == "openai"


class TestLLMClientAnalyze:
    """Tests for LLM analysis calls."""

    def test_analyze_uses_analysis_model(self, mocker):
        """Should use analysis model for analyze calls."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"result": "test"}')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.Anthropic.return_value = mock_client

            client = LLMClient(config)
            client.analyze("test prompt", "system prompt")

            # Verify the analysis model was used
            call_args = mock_client.messages.create.call_args
            assert call_args.kwargs["model"] == "claude-sonnet-4-5-latest"


class TestLLMClientGenerate:
    """Tests for LLM generation calls."""

    def test_generate_uses_generation_model(self, mocker):
        """Should use generation model for generate calls."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
            smart_generation=False,
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='[{"artist": "Test", "title": "Song"}]')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.Anthropic.return_value = mock_client

            client = LLMClient(config)
            client.generate("test prompt", "system prompt")

            # Verify the generation model was used
            call_args = mock_client.messages.create.call_args
            assert call_args.kwargs["model"] == "claude-haiku-4-5-latest"

    def test_smart_generation_uses_analysis_model(self, mocker):
        """Should use analysis model when smart_generation is enabled."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
            smart_generation=True,
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='[{"artist": "Test", "title": "Song"}]')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.Anthropic.return_value = mock_client

            client = LLMClient(config)
            client.generate("test prompt", "system prompt")

            # Verify the analysis model was used for generation
            call_args = mock_client.messages.create.call_args
            assert call_args.kwargs["model"] == "claude-sonnet-4-5-latest"


class TestLLMClientTokenTracking:
    """Tests for token and cost tracking."""

    def test_tracks_tokens_anthropic(self, mocker):
        """Should track tokens for Anthropic calls."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model_analysis="claude-sonnet-4-5-latest",
            model_generation="claude-haiku-4-5-latest",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"result": "test"}')]
        mock_response.usage.input_tokens = 150
        mock_response.usage.output_tokens = 75

        with patch("backend.llm_client.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.Anthropic.return_value = mock_client

            client = LLMClient(config)
            result = client.analyze("test prompt", "system prompt")

            assert result.input_tokens == 150
            assert result.output_tokens == 75
            assert result.total_tokens == 225

    def test_tracks_tokens_openai(self, mocker):
        """Should track tokens for OpenAI calls."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="openai",
            api_key="test-key",
            model_analysis="gpt-4.1",
            model_generation="gpt-4.1-mini",
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"result": "test"}'))]
        mock_response.usage.prompt_tokens = 150
        mock_response.usage.completion_tokens = 75

        with patch("backend.llm_client.openai") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.OpenAI.return_value = mock_client

            client = LLMClient(config)
            result = client.analyze("test prompt", "system prompt")

            assert result.input_tokens == 150
            assert result.output_tokens == 75
            assert result.total_tokens == 225


class TestOllamaProvider:
    """Tests for Ollama provider."""

    def test_ollama_client_init_no_client_created(self):
        """Ollama provider should not create a persistent client."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
            ollama_url="http://localhost:11434",
        )

        client = LLMClient(config)
        assert client.provider == "ollama"
        assert client._client is None  # Ollama uses httpx directly

    def test_complete_ollama_success(self, mocker):
        """Should make completion request to Ollama API."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
            ollama_url="http://localhost:11434",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": '{"result": "test"}',
            "prompt_eval_count": 100,
            "eval_count": 50,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.llm_client.httpx.Client") as mock_httpx:
            mock_client_instance = MagicMock()
            mock_client_instance.post.return_value = mock_response
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_httpx.return_value = mock_client_instance

            client = LLMClient(config)
            result = client._complete_ollama("test prompt", "system prompt", "llama3:8b")

            assert result.content == '{"result": "test"}'
            assert result.input_tokens == 100
            assert result.output_tokens == 50
            assert result.model == "llama3:8b"

            # Verify correct endpoint was called
            mock_client_instance.post.assert_called_once()
            call_args = mock_client_instance.post.call_args
            assert "/api/generate" in call_args[0][0]

    def test_complete_dispatch_routes_to_ollama(self, mocker):
        """Should route 'ollama' provider to _complete_ollama method."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
            ollama_url="http://localhost:11434",
        )

        client = LLMClient(config)

        # Mock the _complete_ollama method
        mock_ollama = mocker.patch.object(client, "_complete_ollama")
        mock_ollama.return_value = MagicMock(content="test")

        client._complete("test prompt", "system prompt", "llama3:8b")

        mock_ollama.assert_called_once_with("test prompt", "system prompt", "llama3:8b")


class TestCustomProvider:
    """Tests for custom OpenAI-compatible provider."""

    def test_custom_client_init_creates_openai_client(self):
        """Custom provider should create OpenAI client with custom base_url."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
            custom_context_window=8192,
        )

        with patch("backend.llm_client.openai") as mock_openai:
            client = LLMClient(config)
            mock_openai.OpenAI.assert_called_once_with(
                api_key="not-needed",
                base_url="http://localhost:5000/v1",
            )
            assert client.provider == "custom"

    def test_complete_custom_success(self, mocker):
        """Should make completion request to custom endpoint."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
            custom_context_window=8192,
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content='{"result": "test"}'))]
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50

        with patch("backend.llm_client.openai") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.OpenAI.return_value = mock_client

            client = LLMClient(config)
            result = client._complete_custom("test prompt", "system prompt", "my-model")

            assert result.content == '{"result": "test"}'
            assert result.input_tokens == 100
            assert result.output_tokens == 50
            assert result.model == "my-model"

    def test_complete_dispatch_routes_to_custom(self, mocker):
        """Should route 'custom' provider to _complete_custom method."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
        )

        with patch("backend.llm_client.openai"):
            client = LLMClient(config)

        # Mock the _complete_custom method
        mock_custom = mocker.patch.object(client, "_complete_custom")
        mock_custom.return_value = MagicMock(content="test")

        client._complete("test prompt", "system prompt", "my-model")

        mock_custom.assert_called_once_with("test prompt", "system prompt", "my-model")


class TestLocalProviderCosts:
    """Tests for local provider cost calculations."""

    def test_ollama_cost_is_zero(self):
        """Ollama provider should have zero cost."""
        from backend.llm_client import get_model_cost
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
        )

        costs = get_model_cost("llama3:8b", config)
        assert costs["input"] == 0.0
        assert costs["output"] == 0.0

    def test_custom_cost_is_zero(self):
        """Custom provider should have zero cost."""
        from backend.llm_client import get_model_cost
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
        )

        costs = get_model_cost("my-model", config)
        assert costs["input"] == 0.0
        assert costs["output"] == 0.0

    def test_estimate_cost_is_zero_for_local(self):
        """estimate_cost_for_model should return 0 for local providers."""
        from backend.llm_client import estimate_cost_for_model
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
        )

        cost = estimate_cost_for_model("llama3:8b", 10000, 5000, config)
        assert cost == 0.0


class TestLocalProviderContextLimits:
    """Tests for local provider context limit lookups."""

    def test_custom_context_from_config(self):
        """Custom provider should use context window from config."""
        from backend.llm_client import get_model_context_limit
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
            custom_context_window=16384,
        )

        limit = get_model_context_limit("my-model", config)
        assert limit == 16384

    def test_ollama_default_context(self):
        """Ollama provider should use default 32768 context."""
        from backend.llm_client import get_model_context_limit
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="llama3:8b",
            model_generation="llama3:8b",
        )

        limit = get_model_context_limit("llama3:8b", config)
        assert limit == 32768

    def test_ollama_context_from_config(self):
        """Ollama provider should use ollama_context_window from config."""
        from backend.llm_client import get_model_context_limit
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="ollama",
            api_key="",
            model_analysis="qwen3:8b",
            model_generation="qwen3:8b",
            ollama_context_window=40960,  # Detected from model info
        )

        limit = get_model_context_limit("qwen3:8b", config)
        assert limit == 40960

    def test_max_tracks_for_custom_model(self):
        """Should calculate max tracks based on custom context window."""
        from backend.llm_client import get_max_tracks_for_model
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="custom",
            api_key="",
            model_analysis="my-model",
            model_generation="my-model",
            custom_url="http://localhost:5000/v1",
            custom_context_window=16384,
        )

        max_tracks = get_max_tracks_for_model("my-model", config=config)
        # (16384 * 0.9 - 1000) / 50 = ~274 tracks
        assert max_tracks > 200  # Should be reasonable number based on 16k context


class TestOllamaModelInfoParsing:
    """Tests for Ollama model info context window parsing."""

    def test_context_from_model_info(self):
        """Should extract context_length from model_info field."""
        from unittest.mock import MagicMock
        from backend.llm_client import get_ollama_model_info

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model_info": {
                "general.architecture": "qwen3",
                "qwen3.context_length": 40960,
            },
            "details": {"parameter_size": "8B"},
            "parameters": "",
            "modelfile": "",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.llm_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response
            result = get_ollama_model_info("http://localhost:11434", "qwen3:8b")

        assert result is not None
        assert result.context_window == 40960
        assert result.parameter_size == "8B"

    def test_num_ctx_overrides_model_info(self):
        """Explicit num_ctx in parameters should override model_info."""
        from unittest.mock import MagicMock
        from backend.llm_client import get_ollama_model_info

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model_info": {
                "general.architecture": "llama",
                "llama.context_length": 8192,
            },
            "details": {},
            "parameters": "num_ctx 4096",  # User-configured override
            "modelfile": "",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.llm_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response
            result = get_ollama_model_info("http://localhost:11434", "llama3:8b")

        assert result is not None
        assert result.context_window == 4096  # num_ctx takes precedence

    def test_fallback_to_default_when_no_context_info(self):
        """Should use 32768 default when no context info available."""
        from unittest.mock import MagicMock
        from backend.llm_client import get_ollama_model_info

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model_info": {},
            "details": {},
            "parameters": "",
            "modelfile": "",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.llm_client.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response
            result = get_ollama_model_info("http://localhost:11434", "unknown-model")

        assert result is not None
        assert result.context_window == 32768


class TestJsonParsing:
    """Tests for JSON parsing from LLM responses."""

    def test_parse_json_with_extra_text(self):
        """Should handle LLM responses with extra text after JSON array."""
        from backend.llm_client import LLMClient, LLMResponse
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        # Simulate LLM adding explanation after JSON
        response = LLMResponse(
            content='[{"artist": "Test", "title": "Song"}]\n\nThis is a great selection because...',
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        result = client.parse_json_response(response)
        assert result == [{"artist": "Test", "title": "Song"}]

    def test_parse_json_with_nested_objects(self):
        """Should handle nested JSON objects with extra text."""
        from backend.llm_client import LLMClient, LLMResponse
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        response = LLMResponse(
            content='{"title": "Test", "tracks": [{"name": "Song"}]} Extra text here',
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        result = client.parse_json_response(response)
        assert result == {"title": "Test", "tracks": [{"name": "Song"}]}

    def test_extract_json_bounds_with_strings_containing_brackets(self):
        """Should handle JSON with brackets inside strings."""
        from backend.llm_client import LLMClient
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        content = '[{"reason": "This track [live] is great"}] Some explanation'
        result = client._extract_json_bounds(content)
        assert result == '[{"reason": "This track [live] is great"}]'

    def test_repair_unescaped_quotes_in_string(self):
        """Should repair unescaped double quotes inside string values."""
        from backend.llm_client import LLMClient, LLMResponse
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        # LLM put quotes around song name inside reason field
        response = LLMResponse(
            content='[{"artist": "Phoenix", "title": "Fences", "reason": "The song "Fences" is great"}]',
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        result = client.parse_json_response(response)
        assert result[0]["artist"] == "Phoenix"
        assert result[0]["title"] == "Fences"
        assert "Fences" in result[0]["reason"]

    def test_repair_multiple_unescaped_quotes(self):
        """Should repair multiple unescaped quotes in one string."""
        from backend.llm_client import LLMClient, LLMResponse
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        response = LLMResponse(
            content='[{"reason": "Both "Song A" and "Song B" are perfect"}]',
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        result = client.parse_json_response(response)
        assert "Song A" in result[0]["reason"]
        assert "Song B" in result[0]["reason"]

    def test_repair_json_with_newlines_in_strings(self):
        """Should handle newlines inside string values."""
        from backend.llm_client import LLMClient, LLMResponse
        from backend.models import LLMConfig

        config = LLMConfig(
            provider="anthropic",
            api_key="test",
            model_analysis="test",
            model_generation="test",
        )

        with patch("backend.llm_client.anthropic"):
            client = LLMClient(config)

        # Note: The actual newline character in the string
        response = LLMResponse(
            content='[{"reason": "Line one\nLine two"}]',
            input_tokens=100,
            output_tokens=50,
            model="test",
        )

        result = client.parse_json_response(response)
        assert "Line one" in result[0]["reason"]
