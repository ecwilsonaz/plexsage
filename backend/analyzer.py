"""Prompt analysis and seed track dimension extraction."""

from backend.llm_client import get_llm_client
from backend.models import (
    AnalyzePromptResponse,
    AnalyzeTrackResponse,
    Dimension,
    GenreCount,
    DecadeCount,
    Track,
)
from backend.plex_client import get_plex_client


PROMPT_ANALYSIS_SYSTEM = """You are a music expert helping to create playlists from a user's music library.

Analyze the user's prompt and suggest appropriate filters (genres and decades) that would help find matching tracks.

Return a JSON object with:
- genres: Array of genre names that match the prompt (e.g., ["Alternative", "Rock", "Indie"])
- decades: Array of decade strings (e.g., ["1990s", "2000s"])
- reasoning: Brief explanation of why you chose these filters

Be specific about genres and decades. Consider:
- Mood/atmosphere (melancholy, upbeat, energetic)
- Era references (90s, classic, modern)
- Genre keywords (alternative, jazz, electronic)
- Artist style hints

Return ONLY valid JSON, no markdown formatting."""


TRACK_ANALYSIS_SYSTEM = """You are a music expert analyzing a song to identify its distinctive characteristics.

Given a track's title, artist, album, and year, identify 5-7 specific musical dimensions that make this track unique. These dimensions will help the user explore similar music.

For each dimension, provide:
- id: A short identifier (e.g., "mood", "era", "instrumentation")
- label: A specific, evocative label (NOT generic like "the mood" - be specific like "The melancholy, bittersweet mood")
- description: A brief explanation of this dimension

Make dimensions SPECIFIC to this track, not generic. Bad: "The genre". Good: "90s British alternative rock with Britpop influences".

Return a JSON object with:
{
  "dimensions": [
    {"id": "mood", "label": "The melancholy, introspective mood", "description": "..."},
    ...
  ]
}

Return ONLY valid JSON, no markdown formatting."""


def analyze_prompt(prompt: str) -> AnalyzePromptResponse:
    """Analyze a natural language prompt to suggest filters.

    Args:
        prompt: User's playlist description

    Returns:
        AnalyzePromptResponse with suggested and available filters

    Raises:
        ValueError: If LLM response cannot be parsed
        RuntimeError: If clients are not initialized
    """
    llm_client = get_llm_client()
    plex_client = get_plex_client()

    if not llm_client:
        raise RuntimeError("LLM client not initialized")
    if not plex_client:
        raise RuntimeError("Plex client not initialized")

    # Get library stats for available filters
    stats = plex_client.get_library_stats()
    available_genres = [GenreCount(**g) for g in stats.get("genres", [])]
    available_decades = [DecadeCount(**d) for d in stats.get("decades", [])]

    # Build prompt with available filter context
    analysis_prompt = f"""User's playlist request: "{prompt}"

Available genres in their library:
{', '.join(f"{g.name} ({g.count})" if g.count else g.name for g in available_genres[:30])}

Available decades in their library:
{', '.join(f"{d.name} ({d.count})" if d.count else d.name for d in available_decades)}

Suggest genres and decades from the available options that best match the user's request."""

    # Call LLM
    response = llm_client.analyze(analysis_prompt, PROMPT_ANALYSIS_SYSTEM)

    # Parse response
    data = llm_client.parse_json_response(response)

    # Filter suggestions to only include available options
    available_genre_names = {g.name for g in available_genres}
    available_decade_names = {d.name for d in available_decades}

    suggested_genres = [
        g for g in data.get("genres", [])
        if g in available_genre_names
    ]
    suggested_decades = [
        d for d in data.get("decades", [])
        if d in available_decade_names
    ]

    return AnalyzePromptResponse(
        suggested_genres=suggested_genres,
        suggested_decades=suggested_decades,
        available_genres=available_genres,
        available_decades=available_decades,
        reasoning=data.get("reasoning", ""),
        token_count=response.total_tokens,
        estimated_cost=response.estimated_cost(),
    )


def analyze_track(track: Track) -> AnalyzeTrackResponse:
    """Analyze a seed track to extract musical dimensions.

    Args:
        track: Track to analyze

    Returns:
        AnalyzeTrackResponse with track and dimensions

    Raises:
        ValueError: If LLM response cannot be parsed
        RuntimeError: If LLM client is not initialized
    """
    llm_client = get_llm_client()

    if not llm_client:
        raise RuntimeError("LLM client not initialized")

    # Build analysis prompt
    analysis_prompt = f"""Analyze this track:
Title: {track.title}
Artist: {track.artist}
Album: {track.album}
Year: {track.year or "Unknown"}
Genres: {", ".join(track.genres) if track.genres else "Unknown"}

Identify 5-7 specific musical dimensions that make this track distinctive."""

    # Call LLM
    response = llm_client.analyze(analysis_prompt, TRACK_ANALYSIS_SYSTEM)

    # Parse response
    data = llm_client.parse_json_response(response)

    dimensions = [
        Dimension(
            id=d.get("id", f"dim_{i}"),
            label=d.get("label", "Unknown dimension"),
            description=d.get("description", ""),
        )
        for i, d in enumerate(data.get("dimensions", []))
    ]

    return AnalyzeTrackResponse(
        track=track,
        dimensions=dimensions,
        token_count=response.total_tokens,
        estimated_cost=response.estimated_cost(),
    )
