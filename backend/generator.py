"""Playlist generation with library validation."""

import json
import logging
import random
from collections.abc import Generator

from backend.llm_client import get_llm_client
from backend.models import GenerateResponse, Track
from backend.plex_client import PlexQueryError, get_plex_client, get_track_cache

logger = logging.getLogger(__name__)


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

        track_cache = get_track_cache()
        has_filters = genres or decades or min_rating > 0

        # Step 1: Fetch tracks - use random sampling when no filters to avoid loading entire library
        if not has_filters:
            # No filters - use efficient random sampling
            yield emit("progress", {"step": "fetching", "message": "Sampling random tracks from library..."})
            sample_size = max_tracks_to_ai if max_tracks_to_ai > 0 else 2000
            logger.info("No filters applied, fetching %d random tracks", sample_size)
            try:
                filtered_tracks = plex_client.get_random_tracks(
                    count=sample_size,
                    exclude_live=exclude_live,
                )
            except PlexQueryError as e:
                yield emit("error", {"message": f"Plex server error: {e}"})
                return
            logger.info("Got %d random tracks", len(filtered_tracks))
        else:
            # Filters applied - check cache or fetch with filters
            cached_tracks = track_cache.get(genres, decades, exclude_live, min_rating)

            if cached_tracks is not None:
                yield emit("progress", {"step": "cache_hit", "message": "Using cached library results..."})
                filtered_tracks = cached_tracks
                logger.info("Using %d cached tracks", len(filtered_tracks))
            else:
                yield emit("progress", {"step": "fetching", "message": "Fetching tracks from library..."})

                logger.info("Fetching tracks with filters: genres=%s, decades=%s, min_rating=%s", genres, decades, min_rating)
                try:
                    filtered_tracks = plex_client.get_tracks_by_filters(
                        genres=genres,
                        decades=decades,
                        exclude_live=exclude_live,
                        min_rating=min_rating,
                    )
                    # Cache for potential future use
                    track_cache.set(genres, decades, exclude_live, min_rating, filtered_tracks)
                except PlexQueryError as e:
                    yield emit("error", {"message": f"Plex server error: {e}"})
                    return

                logger.info("Found %d tracks matching filters", len(filtered_tracks))

        if not filtered_tracks:
            yield emit("error", {"message": "No tracks match the selected filters. Try broadening your selection."})
            return

        # Step 2: Apply limits (only needed when filters are applied)
        if has_filters:
            yield emit("progress", {"step": "filtering", "message": f"Found {len(filtered_tracks)} matching tracks..."})

            if max_tracks_to_ai > 0 and len(filtered_tracks) > max_tracks_to_ai:
                logger.info("Sampling %d tracks from %d", max_tracks_to_ai, len(filtered_tracks))
                filtered_tracks = random.sample(filtered_tracks, max_tracks_to_ai)
            elif len(filtered_tracks) > 2000:
                logger.info("Hard cap: sampling 2000 tracks from %d", len(filtered_tracks))
                filtered_tracks = random.sample(filtered_tracks, 2000)
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

        if seed_track:
            used_keys.add(seed_track.rating_key)

        for selection in track_selections:
            if len(matched_tracks) >= track_count:
                break

            artist = selection.get("artist", "")
            title = selection.get("title", "")

            for track in filtered_tracks:
                if track.rating_key in used_keys:
                    continue

                if _tracks_match(artist, title, track):
                    matched_tracks.append(track)
                    used_keys.add(track.rating_key)
                    break

        # Step 7: Complete
        yield emit("progress", {"step": "complete", "message": "Playlist ready!"})

        result = GenerateResponse(
            tracks=matched_tracks,
            token_count=response.total_tokens,
            estimated_cost=response.estimated_cost(),
        )

        yield emit("complete", result.model_dump(mode="json"))

    except Exception as e:
        logger.exception("Error during playlist generation")
        yield emit("error", {"message": str(e)})


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

    # Get tracks from library
    has_filters = genres or decades or min_rating > 0

    if not has_filters:
        # No filters - use efficient random sampling
        sample_size = max_tracks_to_ai if max_tracks_to_ai > 0 else 2000
        logger.info("No filters applied, fetching %d random tracks", sample_size)
        try:
            filtered_tracks = plex_client.get_random_tracks(
                count=sample_size,
                exclude_live=exclude_live,
            )
        except PlexQueryError as e:
            raise RuntimeError(f"Plex server error while fetching tracks: {e}") from e
        logger.info("Got %d random tracks", len(filtered_tracks))
    else:
        # Filters applied - fetch with filters
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

        # Apply max_tracks_to_ai limit with random sampling
        if max_tracks_to_ai > 0 and len(filtered_tracks) > max_tracks_to_ai:
            logger.info("Sampling %d tracks from %d", max_tracks_to_ai, len(filtered_tracks))
            filtered_tracks = random.sample(filtered_tracks, max_tracks_to_ai)
        elif len(filtered_tracks) > 2000:
            # Hard cap at 2000 to stay within context limits
            logger.info("Hard cap: sampling 2000 tracks from %d", len(filtered_tracks))
            filtered_tracks = random.sample(filtered_tracks, 2000)

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
