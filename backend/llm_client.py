"""LLM client abstraction for Anthropic, OpenAI, and Google Gemini providers."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import anthropic

logger = logging.getLogger(__name__)
import openai
import google.generativeai as genai

from backend.models import LLMConfig


# Cost per million tokens (approximate, updated periodically)
MODEL_COSTS = {
    # Anthropic models (input/output per million tokens)
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.25, "output": 1.25},
    # OpenAI models
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    # Google Gemini models
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
}

# Context limits per model (in tokens) - used to calculate max tracks
MODEL_CONTEXT_LIMITS = {
    # Anthropic
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    # OpenAI
    "gpt-4.1": 128_000,
    "gpt-4.1-mini": 128_000,
    # Google Gemini
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
}

# Tokens per track (approximate)
TOKENS_PER_TRACK = 50


@dataclass
class LLMResponse:
    """Response from an LLM call."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens

    def estimated_cost(self) -> float:
        """Estimate cost in USD based on token usage."""
        costs = MODEL_COSTS.get(self.model, {"input": 1.0, "output": 2.0})
        input_cost = (self.input_tokens / 1_000_000) * costs["input"]
        output_cost = (self.output_tokens / 1_000_000) * costs["output"]
        return input_cost + output_cost


class LLMClient:
    """Unified LLM client for Anthropic and OpenAI."""

    def __init__(self, config: LLMConfig):
        """Initialize LLM client.

        Args:
            config: LLM configuration with provider and API key
        """
        self.config = config
        self.provider = config.provider
        self._client: Any = None

        if config.provider == "anthropic":
            self._client = anthropic.Anthropic(api_key=config.api_key)
        elif config.provider == "openai":
            self._client = openai.OpenAI(api_key=config.api_key)
        elif config.provider == "gemini":
            genai.configure(api_key=config.api_key)
            self._client = genai  # Store the module reference

    def _complete_anthropic(
        self, prompt: str, system: str, model: str
    ) -> LLMResponse:
        """Make a completion request to Anthropic."""
        logger.info("Calling Anthropic API with %d char prompt", len(prompt))
        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        logger.debug("Anthropic response received")

        content = response.content[0].text
        return LLMResponse(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
        )

    def _complete_openai(
        self, prompt: str, system: str, model: str
    ) -> LLMResponse:
        """Make a completion request to OpenAI."""
        logger.info("Calling OpenAI API with %d char prompt", len(prompt))
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        logger.debug("OpenAI response received")

        content = response.choices[0].message.content
        return LLMResponse(
            content=content,
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            model=model,
        )

    def _complete_gemini(
        self, prompt: str, system: str, model: str
    ) -> LLMResponse:
        """Make a completion request to Google Gemini."""
        logger.debug("Creating Gemini model: %s", model)
        gemini_model = self._client.GenerativeModel(
            model_name=model,
            system_instruction=system,
        )

        logger.info("Calling Gemini API with %d char prompt", len(prompt))
        response = gemini_model.generate_content(prompt)
        logger.debug("Gemini response received")

        # Extract token counts from usage metadata
        usage = response.usage_metadata
        return LLMResponse(
            content=response.text,
            input_tokens=usage.prompt_token_count,
            output_tokens=usage.candidates_token_count,
            model=model,
        )

    def _complete(self, prompt: str, system: str, model: str) -> LLMResponse:
        """Make a completion request to the configured provider."""
        if self.provider == "anthropic":
            return self._complete_anthropic(prompt, system, model)
        elif self.provider == "openai":
            return self._complete_openai(prompt, system, model)
        elif self.provider == "gemini":
            return self._complete_gemini(prompt, system, model)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def analyze(self, prompt: str, system: str) -> LLMResponse:
        """Use the analysis model for understanding tasks.

        Args:
            prompt: User prompt to analyze
            system: System prompt with instructions

        Returns:
            LLMResponse with content and token counts
        """
        model = self.config.model_analysis
        return self._complete(prompt, system, model)

    def generate(self, prompt: str, system: str) -> LLMResponse:
        """Use the generation model for track selection.

        If smart_generation is enabled, uses the analysis model instead.

        Args:
            prompt: User prompt for generation
            system: System prompt with instructions

        Returns:
            LLMResponse with content and token counts
        """
        if self.config.smart_generation:
            model = self.config.model_analysis
        else:
            model = self.config.model_generation
        return self._complete(prompt, system, model)

    def parse_json_response(self, response: LLMResponse) -> Any:
        """Parse JSON from LLM response, handling common issues.

        Args:
            response: LLM response to parse

        Returns:
            Parsed JSON data

        Raises:
            ValueError: If JSON cannot be parsed
        """
        content = response.content.strip()

        # Try to extract JSON from markdown code blocks
        # Handles ```json, ```JSON, ``` with any language specifier, or plain ```
        match = re.search(r"```(?:\w+)?\s*\n?(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response as JSON: {e}")


# Global client instance
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient | None:
    """Get the current LLM client instance."""
    return _llm_client


def init_llm_client(config: LLMConfig) -> LLMClient:
    """Initialize or reinitialize the LLM client."""
    global _llm_client
    _llm_client = LLMClient(config)
    return _llm_client


def get_max_tracks_for_model(model: str, buffer_percent: float = 0.10) -> int:
    """Calculate max tracks that can be sent to a model.

    Args:
        model: Model name
        buffer_percent: Buffer to leave (default 10%)

    Returns:
        Maximum number of tracks (0 = no practical limit)
    """
    context_limit = MODEL_CONTEXT_LIMITS.get(model, 128_000)
    usable_tokens = int(context_limit * (1 - buffer_percent))

    # Reserve ~1000 tokens for system prompt and output
    available_for_tracks = usable_tokens - 1000

    max_tracks = available_for_tracks // TOKENS_PER_TRACK
    return max(100, max_tracks)  # Minimum 100 tracks


def get_model_context_limit(model: str) -> int:
    """Get the context limit for a model in tokens."""
    return MODEL_CONTEXT_LIMITS.get(model, 128_000)
