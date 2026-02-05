"""Playlist generation with library validation."""

import logging
import random

from backend.llm_client import get_llm_client

logger = logging.getLogger(__name__)
from backend.models import GenerateResponse, Track
from backend.plex_client import PlexQueryError, get_plex_client


GENERATION_SYSTEM = """You are a music curator creating a playlist from a user's music library.

You will be given:
1. A description of what the user wants (prompt, seed track dimensions, or both)
2. A numbered list of tracks that are available in their library

Your task is to select tracks that best match the user's request. Return your selections as a JSON array of objects with artist, album, and title.

Guidelines:
- Select tracks that fit the mood, era, style, and other aspects of the request
- Vary the selection - don't pick too many tracks from the same artist or album
- Consider the flow of the playlist - how tracks will sound in sequence
- If using a seed track, don't include the seed track itself in the results

Return ONLY a JSON array like:
[
  {"artist": "Artist Name", "album": "Album Name", "title": "Track Title"},
  ...
]

No markdown formatting, no explanations - just the JSON array."""


def generate_playlist(
    prompt: str | None = None,
    seed_track: Track | None = None,
    selected_dimensions: list[str] | None = None,
    additional_notes: str | None = None,
    genres: list[str] | None = None,
    decades: list[str] | None = None,
    track_count: int = 25,
    exclude_live: bool = True,
    min_rating: int = 0,
    max_tracks_to_ai: int = 500,
) -> GenerateResponse:
    """Generate a playlist using the filter-first approach.

    Args:
        prompt: Natural language description (prompt-first flow)
        seed_track: Seed track for finding similar music (seed flow)
        selected_dimensions: Dimension IDs selected by user (seed flow)
        additional_notes: Extra user preferences
        genres: Genre filters to apply
        decades: Decade filters to apply
        track_count: Number of tracks to generate
        exclude_live: Whether to exclude live recordings
        min_rating: Minimum track rating (0-10, 0 = no filter)
        max_tracks_to_ai: Max tracks to send to AI (0 = no limit)

    Returns:
        GenerateResponse with tracks, token count, and estimated cost

    Raises:
        ValueError: If no tracks match filters or LLM response invalid
        RuntimeError: If clients not initialized
    """
    logger.info("Starting playlist generation")
    llm_client = get_llm_client()
    plex_client = get_plex_client()

    if not llm_client:
        raise RuntimeError("LLM client not initialized")
    if not plex_client:
        raise RuntimeError("Plex client not initialized")

    # Get filtered tracks from library
    logger.info("Fetching tracks with filters: genres=%s, decades=%s, min_rating=%s", genres, decades, min_rating)
    try:
        filtered_tracks = plex_client.get_tracks_by_filters(
            genres=genres,
            decades=decades,
            exclude_live=exclude_live,
            min_rating=min_rating,
        )
    except PlexQueryError as e:
        raise RuntimeError(f"Plex server error while fetching tracks: {e}") from e

    logger.info("Found %d tracks matching filters", len(filtered_tracks))

    if not filtered_tracks:
        raise ValueError("No tracks match the selected filters. Try broadening your selection.")

    # Apply max_tracks_to_ai limit with random sampling
    if max_tracks_to_ai > 0 and len(filtered_tracks) > max_tracks_to_ai:
        logger.info("Sampling %d tracks from %d", max_tracks_to_ai, len(filtered_tracks))
        filtered_tracks = random.sample(filtered_tracks, max_tracks_to_ai)
    elif len(filtered_tracks) > 2000:
        # Hard cap at 2000 to stay within context limits
        logger.info("Hard cap: sampling 2000 tracks from %d", len(filtered_tracks))
        filtered_tracks = random.sample(filtered_tracks, 2000)

    # Build the track list for the LLM
    logger.debug("Building track list for %d tracks", len(filtered_tracks))
    track_list = "\n".join(
        f"{i+1}. {t.artist} - {t.title} ({t.album}, {t.year or 'Unknown year'})"
        for i, t in enumerate(filtered_tracks)
    )

    # Build the generation prompt
    generation_parts = []

    if prompt:
        generation_parts.append(f"User's request: {prompt}")

    if seed_track:
        generation_parts.append(
            f"Seed track: {seed_track.title} by {seed_track.artist} "
            f"(from {seed_track.album}, {seed_track.year or 'Unknown year'})"
        )
        if selected_dimensions:
            generation_parts.append(f"Explore these dimensions: {', '.join(selected_dimensions)}")

    if additional_notes:
        generation_parts.append(f"Additional notes: {additional_notes}")

    generation_parts.append(f"\nSelect {track_count} tracks from this library:\n{track_list}")

    generation_prompt = "\n\n".join(generation_parts)

    # Call LLM
    logger.info("Calling LLM with prompt length: %d chars", len(generation_prompt))
    response = llm_client.generate(generation_prompt, GENERATION_SYSTEM)
    logger.info("LLM response received: %d input, %d output tokens", response.input_tokens, response.output_tokens)

    # Parse response
    logger.debug("Parsing JSON response")
    track_selections = llm_client.parse_json_response(response)

    if not isinstance(track_selections, list):
        raise ValueError("LLM returned invalid track selection format")

    # Match LLM selections to library tracks
    matched_tracks: list[Track] = []
    used_keys: set[str] = set()

    # Exclude seed track if present
    if seed_track:
        used_keys.add(seed_track.rating_key)

    # Create a lookup structure for faster matching
    # We'll check each LLM selection against all filtered tracks
    for selection in track_selections:
        if len(matched_tracks) >= track_count:
            break

        artist = selection.get("artist", "")
        title = selection.get("title", "")

        # Find matching track in filtered list
        for track in filtered_tracks:
            if track.rating_key in used_keys:
                continue

            # Use fuzzy matching
            if _tracks_match(artist, title, track):
                matched_tracks.append(track)
                used_keys.add(track.rating_key)
                break

    return GenerateResponse(
        tracks=matched_tracks,
        token_count=response.total_tokens,
        estimated_cost=response.estimated_cost(),
    )


def _tracks_match(llm_artist: str, llm_title: str, library_track: Track) -> bool:
    """Check if LLM selection matches a library track.

    Uses fuzzy matching to handle slight variations in naming.
    """
    from rapidfuzz import fuzz
    from backend.plex_client import simplify_string, normalize_artist, FUZZ_THRESHOLD

    # Compare titles
    simplified_llm_title = simplify_string(llm_title)
    simplified_lib_title = simplify_string(library_track.title)

    if fuzz.ratio(simplified_llm_title, simplified_lib_title) < FUZZ_THRESHOLD:
        return False

    # Compare artists (with variations)
    for artist_variant in normalize_artist(llm_artist):
        simplified_artist = simplify_string(artist_variant)
        simplified_lib_artist = simplify_string(library_track.artist)
        if fuzz.ratio(simplified_artist, simplified_lib_artist) >= FUZZ_THRESHOLD:
            return True

    return False
