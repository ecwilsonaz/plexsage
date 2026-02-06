"""Playlist generation with library validation."""

import json
import logging
from collections.abc import Generator
from datetime import datetime

from backend.llm_client import get_llm_client
from backend.models import GenerateResponse, Track
from backend.plex_client import PlexQueryError, get_plex_client
from backend import library_cache

logger = logging.getLogger(__name__)


def generate_narrative(
    track_selections: list[dict],
    llm_client,
    user_request: str = "",
) -> tuple[str, str]:
    """Generate a creative title and narrative for the playlist.

    Args:
        track_selections: List of track dicts with artist, title, album, reason
        llm_client: LLM client instance
        user_request: Original user prompt/request for context

    Returns:
        Tuple of (playlist_title with date, narrative)
        On failure, returns ("{Mon YYYY} Playlist", "")
    """
    # Build input for Query 2: track list with reasons
    tracks_with_reasons = "\n".join(
        f"- {sel.get('artist', 'Unknown')} - \"{sel.get('title', 'Unknown')}\": {sel.get('reason', 'Selected for this playlist')}"
        for sel in track_selections[:15]  # Limit to first 15 for context efficiency
    )

    # Include user request for context
    if user_request:
        narrative_prompt = f"User's request: {user_request}\n\nSelected tracks:\n{tracks_with_reasons}"
    else:
        narrative_prompt = f"Selected tracks:\n{tracks_with_reasons}"

    # Get current month/year for title suffix
    date_suffix = datetime.now().strftime("%b %Y")
    fallback_title = f"{date_suffix} Playlist"

    try:
        # Use analysis model for better creative writing quality
        response = llm_client.analyze(narrative_prompt, NARRATIVE_SYSTEM)
        result = llm_client.parse_json_response(response)

        if not isinstance(result, dict):
            logger.warning("Narrative response not a dict, using fallback")
            return fallback_title, ""

        raw_title = result.get("title", "").strip()
        narrative = result.get("narrative", "").strip()

        # Append date to title
        if raw_title:
            playlist_title = f"{raw_title} - {date_suffix}"
        else:
            playlist_title = fallback_title

        return playlist_title, narrative

    except Exception as e:
        logger.warning("Narrative generation failed: %s", e)
        return fallback_title, ""


def _cached_track_to_model(cached: dict) -> Track:
    """Convert a cached track dict to a Track model."""
    return Track(
        rating_key=cached["rating_key"],
        title=cached["title"],
        artist=cached["artist"],
        album=cached["album"],
        duration_ms=cached.get("duration_ms") or 0,
        year=cached.get("year"),
        genres=cached.get("genres") or [],
        art_url=f"/api/art/{cached['rating_key']}",
    )


def _get_tracks_from_cache_or_plex(
    plex_client,
    genres: list[str] | None,
    decades: list[str] | None,
    exclude_live: bool,
    min_rating: int,
    max_tracks_to_ai: int,
) -> list[Track]:
    """Get tracks from cache if available, otherwise from Plex.

    Returns:
        List of Track objects
    """
    has_filters = genres or decades or min_rating > 0
    effective_limit = max_tracks_to_ai if max_tracks_to_ai > 0 else 2000

    # Try cache first
    if library_cache.has_cached_tracks():
        logger.info("Using cached tracks for generation")
        cached_tracks = library_cache.get_tracks_by_filters(
            genres=genres,
            decades=decades,
            min_rating=min_rating,
            exclude_live=exclude_live,
            limit=effective_limit,
        )
        return [_cached_track_to_model(t) for t in cached_tracks]

    # Fall back to Plex
    logger.info("Cache empty, fetching from Plex")
    if not has_filters:
        return plex_client.get_random_tracks(
            count=effective_limit,
            exclude_live=exclude_live,
        )
    else:
        return plex_client.get_tracks_by_filters(
            genres=genres,
            decades=decades,
            exclude_live=exclude_live,
            min_rating=min_rating,
            limit=effective_limit,
        )


def generate_playlist_stream(
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
) -> Generator[str, None, None]:
    """Generate a playlist with streaming progress updates.

    Yields SSE-formatted events with progress updates and final result.
    """
    def emit(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    try:
        logger.info("Starting playlist generation (streaming)")
        llm_client = get_llm_client()
        plex_client = get_plex_client()

        if not llm_client:
            yield emit("error", {"message": "LLM client not initialized"})
            return
        if not plex_client:
            yield emit("error", {"message": "Plex client not initialized"})
            return

        has_filters = genres or decades or min_rating > 0

        # Step 1: Fetch tracks from cache or Plex
        using_cache = library_cache.has_cached_tracks()
        if using_cache:
            yield emit("progress", {"step": "fetching", "message": "Loading tracks from cache..."})
        elif not has_filters:
            yield emit("progress", {"step": "fetching", "message": "Sampling random tracks from library..."})
        else:
            yield emit("progress", {"step": "fetching", "message": "Fetching tracks from library..."})

        logger.info("Fetching tracks: genres=%s, decades=%s, min_rating=%s, using_cache=%s",
                    genres, decades, min_rating, using_cache)
        try:
            filtered_tracks = _get_tracks_from_cache_or_plex(
                plex_client=plex_client,
                genres=genres,
                decades=decades,
                exclude_live=exclude_live,
                min_rating=min_rating,
                max_tracks_to_ai=max_tracks_to_ai,
            )
        except PlexQueryError as e:
            yield emit("error", {"message": f"Plex server error: {e}"})
            return

        logger.info("Got %d tracks", len(filtered_tracks))

        if not filtered_tracks:
            yield emit("error", {"message": "No tracks match the selected filters. Try broadening your selection."})
            return

        # Step 2: Report track count (sampling already done server-side)
        if has_filters:
            yield emit("progress", {"step": "filtering", "message": f"Using {len(filtered_tracks)} tracks..."})
        else:
            yield emit("progress", {"step": "filtering", "message": f"Using {len(filtered_tracks)} random tracks..."})

        # Step 3: Build track list
        yield emit("progress", {"step": "preparing", "message": f"Preparing {len(filtered_tracks)} tracks for AI..."})

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

        # Step 4: Call LLM
        yield emit("progress", {"step": "ai_working", "message": "AI is curating your playlist..."})

        logger.info("Calling LLM with prompt length: %d chars", len(generation_prompt))
        response = llm_client.generate(generation_prompt, GENERATION_SYSTEM)
        logger.info("LLM response received: %d input, %d output tokens", response.input_tokens, response.output_tokens)

        # Step 5: Parse response
        yield emit("progress", {"step": "parsing", "message": "Parsing AI selections..."})

        track_selections = llm_client.parse_json_response(response)

        if not isinstance(track_selections, list):
            yield emit("error", {"message": "LLM returned invalid track selection format"})
            return

        # Step 6: Match tracks
        yield emit("progress", {"step": "matching", "message": f"Matching {len(track_selections)} selections to library..."})

        matched_tracks: list[Track] = []
        used_keys: set[str] = set()
        track_reasons: dict[str, str] = {}

        if seed_track:
            used_keys.add(seed_track.rating_key)

        for selection in track_selections:
            if len(matched_tracks) >= track_count:
                break

            artist = selection.get("artist", "")
            title = selection.get("title", "")
            reason = selection.get("reason", "")

            for track in filtered_tracks:
                if track.rating_key in used_keys:
                    continue

                if _tracks_match(artist, title, track):
                    matched_tracks.append(track)
                    used_keys.add(track.rating_key)
                    if reason:
                        track_reasons[track.rating_key] = reason
                    break

        # Step 7: Generate narrative
        yield emit("progress", {"step": "narrative", "message": "Writing playlist narrative..."})

        playlist_title, narrative = generate_narrative(track_selections, llm_client, prompt or "")
        logger.info("Generated narrative: title='%s', narrative_len=%d", playlist_title, len(narrative))

        # Emit narrative event for frontend
        yield emit("narrative", {
            "playlist_title": playlist_title,
            "narrative": narrative,
            "track_reasons": track_reasons,
            "user_request": prompt or "",
        })

        # Step 8: Complete
        logger.info("Track matching complete. Matched %d tracks", len(matched_tracks))
        logger.info("Emitting 'Playlist ready!' progress event")
        yield emit("progress", {"step": "complete", "message": "Playlist ready!"})

        logger.info("Building GenerateResponse: tokens=%s, cost=%s",
                    getattr(response, 'total_tokens', 'N/A'),
                    response.estimated_cost() if response else 'N/A')

        try:
            result = GenerateResponse(
                tracks=matched_tracks,
                token_count=response.total_tokens,
                estimated_cost=response.estimated_cost(),
                playlist_title=playlist_title,
                narrative=narrative,
                track_reasons=track_reasons,
            )
            logger.info("GenerateResponse built successfully with %d tracks", len(result.tracks))
        except Exception as e:
            logger.exception("Failed to build GenerateResponse: %s", e)
            yield emit("error", {"message": f"Failed to build response: {e}"})
            return

        logger.info("Emitting complete event")
        yield emit("complete", result.model_dump(mode="json"))
        logger.info("Complete event emitted successfully")

    except Exception as e:
        logger.exception("Error during playlist generation")
        yield emit("error", {"message": str(e)})


GENERATION_SYSTEM = """You are a music curator creating a playlist from a user's music library.

You will be given:
1. A description of what the user wants (prompt, seed track dimensions, or both)
2. A numbered list of tracks that are available in their library

Your task is to select tracks that best match the user's request. For each track, include a brief reason (1 sentence) explaining why it fits.

Guidelines:
- Select tracks that fit the mood, era, style, and other aspects of the request
- Vary the selection - don't pick too many tracks from the same artist or album
- Consider the flow of the playlist - how tracks will sound in sequence
- If using a seed track, don't include the seed track itself in the results

Return ONLY a JSON array like:
[
  {"artist": "Artist Name", "album": "Album Name", "title": "Track Title", "reason": "Brief explanation of why this track fits."},
  ...
]

No markdown formatting, no explanations - just the JSON array."""


NARRATIVE_SYSTEM = """You are a music connoisseur writing a brief liner note for a playlist.

Given the user's original request and the track selections (with reasons), create:
1. A creative playlist title (2-5 words, evocative, do NOT include any date)
2. A brief narrative (3 sentences, under 400 characters) that:
   - Reflects the mood or theme the user asked for
   - Mentions 3-4 specific songs by name (use single quotes around song names, e.g. 'Skinny Love')

Sound like a passionate music lover. Be concise.

Return ONLY valid JSON:
{"title": "Creative Title Here", "narrative": "Your brief narrative with 'song names' in single quotes..."}

No markdown formatting, no explanations - just the JSON object."""


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

    # Get tracks from cache or Plex
    using_cache = library_cache.has_cached_tracks()

    logger.info("Fetching tracks: genres=%s, decades=%s, min_rating=%s, using_cache=%s",
                genres, decades, min_rating, using_cache)
    try:
        filtered_tracks = _get_tracks_from_cache_or_plex(
            plex_client=plex_client,
            genres=genres,
            decades=decades,
            exclude_live=exclude_live,
            min_rating=min_rating,
            max_tracks_to_ai=max_tracks_to_ai,
        )
    except PlexQueryError as e:
        raise RuntimeError(f"Plex server error while fetching tracks: {e}") from e

    logger.info("Got %d tracks", len(filtered_tracks))

    if not filtered_tracks:
        raise ValueError("No tracks match the selected filters. Try broadening your selection.")

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
    track_reasons: dict[str, str] = {}

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
        reason = selection.get("reason", "")

        # Find matching track in filtered list
        for track in filtered_tracks:
            if track.rating_key in used_keys:
                continue

            # Use fuzzy matching
            if _tracks_match(artist, title, track):
                matched_tracks.append(track)
                used_keys.add(track.rating_key)
                if reason:
                    track_reasons[track.rating_key] = reason
                break

    # Generate narrative
    playlist_title, narrative = generate_narrative(track_selections, llm_client, prompt or "")
    logger.info("Generated narrative: title='%s', narrative_len=%d", playlist_title, len(narrative))

    return GenerateResponse(
        tracks=matched_tracks,
        token_count=response.total_tokens,
        estimated_cost=response.estimated_cost(),
        playlist_title=playlist_title,
        narrative=narrative,
        track_reasons=track_reasons,
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
