"""Pydantic models for PlexSage API contracts and internal data structures."""

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


# =============================================================================
# Core Entities
# =============================================================================


class Track(BaseModel):
    """A music track from the Plex library."""

    rating_key: str
    title: str
    artist: str
    album: str
    duration_ms: int
    year: int | None = None
    genres: list[str] = []
    art_url: str | None = None

    @property
    def duration_formatted(self) -> str:
        """Return duration as M:SS format."""
        minutes = self.duration_ms // 60000
        seconds = (self.duration_ms % 60000) // 1000
        return f"{minutes}:{seconds:02d}"


class Dimension(BaseModel):
    """A musical dimension identified from a seed track."""

    id: str
    label: str
    description: str


class FilterSet(BaseModel):
    """Filters applied to narrow track selection."""

    genres: list[str] = []
    decades: list[str] = []
    track_count: int = 25
    exclude_live: bool = True

    @field_validator("track_count")
    @classmethod
    def validate_track_count(cls, v: int) -> int:
        if v not in [15, 25, 40]:
            raise ValueError("track_count must be 15, 25, or 40")
        return v


class Playlist(BaseModel):
    """A generated playlist with tracks and metadata."""

    name: str
    tracks: list[Track]
    source_prompt: str | None = None
    seed_track_key: str | None = None
    selected_dimensions: list[str] | None = None

    @property
    def duration_total(self) -> int:
        """Total duration in milliseconds."""
        return sum(t.duration_ms for t in self.tracks)

    @property
    def track_count(self) -> int:
        return len(self.tracks)


# =============================================================================
# Configuration Models
# =============================================================================


class PlexConfig(BaseModel):
    """Plex server connection settings."""

    url: str
    token: str
    music_library: str = "Music"


class LLMConfig(BaseModel):
    """LLM provider settings."""

    provider: Literal["anthropic", "openai", "gemini", "ollama", "custom"]
    api_key: str = ""  # Optional for local providers
    model_analysis: str
    model_generation: str
    smart_generation: bool = False
    # Local provider settings
    ollama_url: str = "http://localhost:11434"
    ollama_context_window: int = 32768  # Detected from model, can be overridden
    custom_url: str = ""
    custom_context_window: int = 32768

    @field_validator("ollama_context_window", "custom_context_window")
    @classmethod
    def validate_context_window(cls, v: int) -> int:
        if v < 512:
            raise ValueError("Context window must be at least 512 tokens")
        if v > 2000000:
            raise ValueError("Context window cannot exceed 2,000,000 tokens")
        return v


class DefaultsConfig(BaseModel):
    """Default values for UI."""

    track_count: int = 25


class AppConfig(BaseModel):
    """Root configuration object."""

    plex: PlexConfig
    llm: LLMConfig
    defaults: DefaultsConfig = DefaultsConfig()


# =============================================================================
# API Request/Response Models
# =============================================================================


class GenreCount(BaseModel):
    """Genre with track count."""

    name: str
    count: int | None = None


class DecadeCount(BaseModel):
    """Decade with track count."""

    name: str
    count: int | None = None


class LibraryStatsResponse(BaseModel):
    """Library statistics response."""

    total_tracks: int
    genres: list[GenreCount]
    decades: list[DecadeCount]


class AnalyzePromptRequest(BaseModel):
    """Request to analyze a natural language prompt."""

    prompt: str


class AnalyzePromptResponse(BaseModel):
    """Response from prompt analysis."""

    suggested_genres: list[str]
    suggested_decades: list[str]
    available_genres: list[GenreCount]
    available_decades: list[DecadeCount]
    reasoning: str
    token_count: int = 0
    estimated_cost: float = 0.0


class AnalyzeTrackRequest(BaseModel):
    """Request to analyze a seed track for dimensions."""

    rating_key: str


class AnalyzeTrackResponse(BaseModel):
    """Response from track analysis."""

    track: Track
    dimensions: list[Dimension]
    token_count: int = 0
    estimated_cost: float = 0.0


class FilterPreviewRequest(BaseModel):
    """Request to preview filter results."""

    genres: list[str] = []
    decades: list[str] = []
    track_count: int = 25
    max_tracks_to_ai: int = 500  # 0 = no limit
    min_rating: int = 0  # 0 = any, 2/4/6/8/10 = minimum rating (Plex uses 0-10)
    exclude_live: bool = True


class FilterPreviewResponse(BaseModel):
    """Response with filter preview stats."""

    matching_tracks: int  # -1 if unknown
    tracks_to_send: int  # How many will actually be sent to AI
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost: float


class SeedTrackInput(BaseModel):
    """Seed track input for generation."""

    rating_key: str
    selected_dimensions: list[str]


class GenerateRequest(BaseModel):
    """Request to generate a playlist."""

    prompt: str | None = None
    seed_track: SeedTrackInput | None = None
    additional_notes: str | None = None
    genres: list[str]
    decades: list[str]
    track_count: int = 25
    exclude_live: bool = True
    min_rating: int = 0  # 0 = any, 2/4/6/8/10 = minimum rating
    max_tracks_to_ai: int = 500  # 0 = no limit

    @model_validator(mode="after")
    def check_flow(self) -> "GenerateRequest":
        if not self.prompt and not self.seed_track:
            raise ValueError("Either prompt or seed_track must be provided")
        return self


class GenerateResponse(BaseModel):
    """Response from playlist generation."""

    tracks: list[Track]
    token_count: int
    estimated_cost: float


class SavePlaylistRequest(BaseModel):
    """Request to save a playlist to Plex."""

    name: str
    rating_keys: list[str]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Playlist name cannot be empty")
        return v.strip()

    @field_validator("rating_keys")
    @classmethod
    def validate_rating_keys(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one track is required")
        return v


class SavePlaylistResponse(BaseModel):
    """Response from saving a playlist."""

    success: bool
    playlist_id: str | None = None
    playlist_url: str | None = None
    error: str | None = None
    tracks_added: int | None = None
    tracks_skipped: int | None = None


class ConfigResponse(BaseModel):
    """Config without secrets for display."""

    plex_url: str
    plex_connected: bool
    plex_token_set: bool  # True if token is configured (without revealing it)
    music_library: str | None
    llm_provider: str
    llm_configured: bool
    llm_api_key_set: bool  # True if API key is configured (without revealing it)
    model_analysis: str  # The analysis model being used
    model_generation: str  # The generation model being used
    max_tracks_to_ai: int  # Recommended max tracks for this model
    cost_per_million_input: float  # Cost per million input tokens for generation model
    cost_per_million_output: float  # Cost per million output tokens for generation model
    defaults: DefaultsConfig
    # Local provider fields
    ollama_url: str = "http://localhost:11434"
    ollama_context_window: int = 32768
    custom_url: str = ""
    custom_context_window: int = 32768
    is_local_provider: bool = False
    provider_from_env: bool = False  # True if LLM_PROVIDER env var is overriding UI


class UpdateConfigRequest(BaseModel):
    """Partial config update."""

    plex_url: str | None = None
    plex_token: str | None = None
    music_library: str | None = None
    llm_provider: str | None = None
    llm_api_key: str | None = None
    model_analysis: str | None = None
    model_generation: str | None = None
    # Local provider fields
    ollama_url: str | None = None
    ollama_context_window: int | None = None
    custom_url: str | None = None
    custom_context_window: int | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    plex_connected: bool
    llm_configured: bool


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: str | None = None


# =============================================================================
# Ollama API Models
# =============================================================================


class OllamaModel(BaseModel):
    """A model available in Ollama."""

    name: str
    size: int = 0
    modified_at: str = ""


class OllamaModelInfo(BaseModel):
    """Detailed info about an Ollama model."""

    name: str
    context_window: int
    context_detected: bool = True  # False if using fallback default
    parameter_size: str | None = None


class OllamaModelsResponse(BaseModel):
    """Response from listing Ollama models."""

    models: list[OllamaModel] = []
    error: str | None = None


class OllamaStatus(BaseModel):
    """Connection status for Ollama."""

    connected: bool
    model_count: int = 0
    error: str | None = None
