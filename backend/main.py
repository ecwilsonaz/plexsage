"""FastAPI application for MediaSage."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse
import httpx

from backend.config import get_config, update_config_values
from backend.version import get_version
from backend.models import (
    AnalyzePromptRequest,
    AnalyzePromptResponse,
    AnalyzeTrackRequest,
    AnalyzeTrackResponse,
    ConfigResponse,
    FilterPreviewRequest,
    FilterPreviewResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    LibraryCacheStatusResponse,
    LibraryStatsResponse,
    GenreCount,
    DecadeCount,
    SavePlaylistRequest,
    SavePlaylistResponse,
    SyncProgress,
    SyncTriggerResponse,
    Track,
    UpdateConfigRequest,
)
from backend.plex_client import get_plex_client, init_plex_client
from backend import library_cache
from backend.version import get_version
from backend.llm_client import (
    get_llm_client,
    init_llm_client,
    get_max_tracks_for_model,
    get_model_cost,
    estimate_cost_for_model,
    list_ollama_models,
    get_ollama_model_info,
    get_ollama_status,
)
from backend.models import OllamaModelsResponse, OllamaModelInfo, OllamaStatus
from backend.analyzer import analyze_prompt as do_analyze_prompt, analyze_track as do_analyze_track
from backend.generator import generate_playlist as do_generate_playlist, generate_playlist_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize clients on startup."""
    config = get_config()

    # Initialize Plex client if configured
    if config.plex.url and config.plex.token:
        init_plex_client(
            config.plex.url,
            config.plex.token,
            config.plex.music_library,
        )

    # Initialize LLM client if configured
    # Local providers (ollama, custom) don't need an API key
    if config.llm.api_key or config.llm.provider in ("ollama", "custom"):
        init_llm_client(config.llm)

    yield


app = FastAPI(
    title="MediaSage",
    description="Plex playlist generator powered by LLMs",
    version=get_version(),
    lifespan=lifespan,
)


# =============================================================================
# Health Endpoint
# =============================================================================


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check application health status."""
    config = get_config()
    plex_client = get_plex_client()

    # Check actual Plex connection
    plex_connected = plex_client.is_connected() if plex_client else False

    # Check if LLM is configured (API key for cloud, URL for local providers)
    llm_configured = bool(config.llm.api_key)
    if config.llm.provider == "ollama" and config.llm.ollama_url:
        llm_configured = True
    elif config.llm.provider == "custom" and config.llm.custom_url:
        llm_configured = True

    return HealthResponse(
        status="healthy",
        plex_connected=plex_connected,
        llm_configured=llm_configured,
    )


# =============================================================================
# Configuration Endpoints
# =============================================================================


@app.get("/api/config", response_model=ConfigResponse)
async def get_configuration() -> ConfigResponse:
    """Get current configuration (without secrets)."""
    config = get_config()
    plex_client = get_plex_client()

    # Calculate recommended max tracks based on generation model
    generation_model = config.llm.model_generation
    analysis_model = config.llm.model_analysis
    max_tracks = get_max_tracks_for_model(generation_model, config=config.llm)

    # Get cost rates for generation model (for client-side cost calculation)
    # Local providers have zero cost
    is_local = config.llm.provider in ("ollama", "custom")
    costs = get_model_cost(generation_model, config.llm)

    # LLM is configured if we have an API key OR if using a local provider with URL
    llm_configured = bool(config.llm.api_key)
    if config.llm.provider == "ollama" and config.llm.ollama_url:
        llm_configured = True
    elif config.llm.provider == "custom" and config.llm.custom_url:
        llm_configured = True

    # Check if provider is being set by environment variable
    provider_from_env = os.environ.get("LLM_PROVIDER") is not None

    return ConfigResponse(
        version=get_version(),
        plex_url=config.plex.url,
        plex_connected=plex_client.is_connected() if plex_client else False,
        plex_token_set=bool(config.plex.token),
        music_library=config.plex.music_library,
        llm_provider=config.llm.provider,
        llm_configured=llm_configured,
        llm_api_key_set=bool(config.llm.api_key),
        model_analysis=analysis_model,
        model_generation=generation_model,
        max_tracks_to_ai=max_tracks,
        cost_per_million_input=costs["input"],
        cost_per_million_output=costs["output"],
        defaults=config.defaults,
        ollama_url=config.llm.ollama_url,
        ollama_context_window=config.llm.ollama_context_window,
        custom_url=config.llm.custom_url,
        custom_context_window=config.llm.custom_context_window,
        is_local_provider=is_local,
        provider_from_env=provider_from_env,
    )


@app.post("/api/config", response_model=ConfigResponse)
async def update_configuration(request: UpdateConfigRequest) -> ConfigResponse:
    """Update configuration values."""
    # Convert request to dict, filtering out None values
    updates = {
        k: v
        for k, v in request.model_dump().items()
        if v is not None
    }

    if not updates:
        raise HTTPException(status_code=400, detail="No configuration values provided")

    # Update config
    config = update_config_values(updates)

    # Reinitialize clients if relevant config changed
    if any(k in updates for k in ["plex_url", "plex_token", "music_library"]):
        init_plex_client(
            config.plex.url,
            config.plex.token,
            config.plex.music_library,
        )

    if any(k in updates for k in ["llm_provider", "llm_api_key", "model_analysis", "model_generation", "ollama_url", "custom_url"]):
        init_llm_client(config.llm)

    plex_client = get_plex_client()

    # Calculate recommended max tracks based on generation model
    generation_model = config.llm.model_generation
    analysis_model = config.llm.model_analysis
    max_tracks = get_max_tracks_for_model(generation_model, config=config.llm)

    # Get cost rates for generation model (for client-side cost calculation)
    # Local providers have zero cost
    is_local = config.llm.provider in ("ollama", "custom")
    costs = get_model_cost(generation_model, config.llm)

    # LLM is configured if we have an API key OR if using a local provider with URL
    llm_configured = bool(config.llm.api_key)
    if config.llm.provider == "ollama" and config.llm.ollama_url:
        llm_configured = True
    elif config.llm.provider == "custom" and config.llm.custom_url:
        llm_configured = True

    # Check if provider is being set by environment variable
    provider_from_env = os.environ.get("LLM_PROVIDER") is not None

    return ConfigResponse(
        version=get_version(),
        plex_url=config.plex.url,
        plex_connected=plex_client.is_connected() if plex_client else False,
        plex_token_set=bool(config.plex.token),
        music_library=config.plex.music_library,
        llm_provider=config.llm.provider,
        llm_configured=llm_configured,
        llm_api_key_set=bool(config.llm.api_key),
        model_analysis=analysis_model,
        model_generation=generation_model,
        max_tracks_to_ai=max_tracks,
        cost_per_million_input=costs["input"],
        cost_per_million_output=costs["output"],
        defaults=config.defaults,
        ollama_url=config.llm.ollama_url,
        ollama_context_window=config.llm.ollama_context_window,
        custom_url=config.llm.custom_url,
        custom_context_window=config.llm.custom_context_window,
        is_local_provider=is_local,
        provider_from_env=provider_from_env,
    )


# =============================================================================
# Ollama Endpoints
# =============================================================================


@app.get("/api/ollama/status", response_model=OllamaStatus)
async def ollama_status(
    url: str | None = Query(None, description="Ollama URL (optional, defaults to config)")
) -> OllamaStatus:
    """Check Ollama connection status."""
    config = get_config()
    ollama_url = url or config.llm.ollama_url
    return await asyncio.to_thread(get_ollama_status, ollama_url)


@app.get("/api/ollama/models", response_model=OllamaModelsResponse)
async def ollama_models(
    url: str | None = Query(None, description="Ollama URL (optional, defaults to config)")
) -> OllamaModelsResponse:
    """List available Ollama models."""
    config = get_config()
    ollama_url = url or config.llm.ollama_url
    return await asyncio.to_thread(list_ollama_models, ollama_url)


@app.get("/api/ollama/model-info", response_model=OllamaModelInfo | None)
async def ollama_model_info(
    model: str = Query(..., description="Model name"),
    url: str | None = Query(None, description="Ollama URL (optional, defaults to config)")
) -> OllamaModelInfo | None:
    """Get detailed info about an Ollama model."""
    config = get_config()
    ollama_url = url or config.llm.ollama_url
    info = await asyncio.to_thread(get_ollama_model_info, ollama_url, model)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")
    return info


# =============================================================================
# Library Cache Endpoints
# =============================================================================


@app.get("/api/library/status", response_model=LibraryCacheStatusResponse)
async def get_library_status() -> LibraryCacheStatusResponse:
    """Get library cache status for UI polling."""
    plex_client = get_plex_client()

    # Get sync state from cache module
    state = library_cache.get_sync_state()

    # Build response
    sync_progress = None
    if state["sync_progress"]:
        sync_progress = SyncProgress(
            phase=state["sync_progress"]["phase"],
            current=state["sync_progress"]["current"],
            total=state["sync_progress"]["total"],
        )

    return LibraryCacheStatusResponse(
        track_count=state["track_count"],
        synced_at=state["synced_at"],
        is_syncing=state["is_syncing"],
        sync_progress=sync_progress,
        error=state["error"],
        plex_connected=plex_client.is_connected() if plex_client else False,
    )


@app.post("/api/library/sync", response_model=SyncTriggerResponse)
async def trigger_library_sync() -> SyncTriggerResponse:
    """Trigger library sync from Plex.

    Always starts sync in background so progress can be polled.
    """
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    # Check if already syncing
    progress = library_cache.get_sync_progress()
    if progress["is_syncing"]:
        raise HTTPException(status_code=409, detail="Sync already in progress")

    # Always run sync in background so progress can be polled
    asyncio.create_task(
        asyncio.to_thread(library_cache.sync_library, plex_client)
    )
    return SyncTriggerResponse(started=True, blocking=False)


# =============================================================================
# Library Endpoints
# =============================================================================


@app.get("/api/library/stats", response_model=LibraryStatsResponse)
async def get_library_stats() -> LibraryStatsResponse:
    """Get library statistics."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    stats = await asyncio.to_thread(plex_client.get_library_stats)
    return LibraryStatsResponse(
        total_tracks=stats.get("total_tracks", 0),
        genres=[GenreCount(**g) for g in stats.get("genres", [])],
        decades=[DecadeCount(**d) for d in stats.get("decades", [])],
    )


@app.get("/api/library/search", response_model=list[Track])
async def search_library(q: str = Query(..., description="Search query")) -> list[Track]:
    """Search for tracks in the library."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    return await asyncio.to_thread(plex_client.search_tracks, q)


# =============================================================================
# Analysis Endpoints
# =============================================================================


@app.post("/api/analyze/prompt", response_model=AnalyzePromptResponse)
async def analyze_prompt(request: AnalyzePromptRequest) -> AnalyzePromptResponse:
    """Analyze a natural language prompt to suggest filters."""
    plex_client = get_plex_client()
    llm_client = get_llm_client()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    try:
        return await asyncio.to_thread(do_analyze_prompt, request.prompt)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.post("/api/analyze/track", response_model=AnalyzeTrackResponse)
async def analyze_track(request: AnalyzeTrackRequest) -> AnalyzeTrackResponse:
    """Analyze a seed track for dimensions."""
    plex_client = get_plex_client()
    llm_client = get_llm_client()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # Get the track
    track = await asyncio.to_thread(plex_client.get_track_by_key, request.rating_key)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    try:
        return await asyncio.to_thread(do_analyze_track, track)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.post("/api/filter/preview", response_model=FilterPreviewResponse)
async def preview_filters(request: FilterPreviewRequest) -> FilterPreviewResponse:
    """Preview filter results with track count and cost estimate.

    Uses local cache when available for instant response, falls back to
    Plex query if cache is empty.
    """
    plex_client = get_plex_client()
    config = get_config()

    genres = request.genres if request.genres else None
    decades = request.decades if request.decades else None
    exclude_live = request.exclude_live
    min_rating = request.min_rating

    # Try cache first for instant response
    matching_tracks = -1
    if library_cache.has_cached_tracks():
        matching_tracks = await asyncio.to_thread(
            library_cache.count_tracks_by_filters,
            genres=genres,
            decades=decades,
            min_rating=min_rating,
            exclude_live=exclude_live,
        )

    # Fall back to Plex if cache is empty
    if matching_tracks < 0:
        if not plex_client or not plex_client.is_connected():
            raise HTTPException(status_code=503, detail="Plex not connected")

        matching_tracks = await asyncio.to_thread(
            plex_client.count_tracks_by_filters,
            genres=genres,
            decades=decades,
            exclude_live=exclude_live,
            min_rating=min_rating,
        )

    # Calculate how many tracks will actually be sent to AI
    if matching_tracks <= 0:
        tracks_to_send = 0
    elif request.max_tracks_to_ai == 0:  # No limit
        tracks_to_send = matching_tracks
    else:
        tracks_to_send = min(matching_tracks, request.max_tracks_to_ai)

    # Estimate tokens for the generation request
    # Rough estimates: ~50 tokens per track in context, ~30 tokens per track in output
    estimated_input_tokens = 500 + (tracks_to_send * 50)  # Base prompt + track list
    estimated_output_tokens = request.track_count * 30  # ~30 tokens per track suggestion

    # Calculate cost using the actual configured generation model
    estimated_cost = estimate_cost_for_model(
        config.llm.model_generation,
        estimated_input_tokens,
        estimated_output_tokens,
        config=config.llm,
    )

    return FilterPreviewResponse(
        matching_tracks=matching_tracks,
        tracks_to_send=tracks_to_send,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_cost=estimated_cost,
    )


# =============================================================================
# Generation Endpoints
# =============================================================================


@app.post("/api/generate", response_model=GenerateResponse)
async def generate_playlist(request: GenerateRequest) -> GenerateResponse:
    """Generate a playlist."""
    plex_client = get_plex_client()
    llm_client = get_llm_client()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # Get seed track if provided
    seed_track = None
    selected_dimensions = None
    if request.seed_track:
        seed_track = await asyncio.to_thread(
            plex_client.get_track_by_key, request.seed_track.rating_key
        )
        if not seed_track:
            raise HTTPException(status_code=404, detail="Seed track not found")
        selected_dimensions = request.seed_track.selected_dimensions

    try:
        return await asyncio.to_thread(
            do_generate_playlist,
            prompt=request.prompt,
            seed_track=seed_track,
            selected_dimensions=selected_dimensions,
            additional_notes=request.additional_notes,
            genres=request.genres,
            decades=request.decades,
            track_count=request.track_count,
            exclude_live=request.exclude_live,
            min_rating=request.min_rating,
            max_tracks_to_ai=request.max_tracks_to_ai,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")


@app.post("/api/generate/stream")
async def generate_playlist_sse(request: GenerateRequest) -> StreamingResponse:
    """Generate a playlist with streaming progress updates."""
    plex_client = get_plex_client()
    llm_client = get_llm_client()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # Get seed track if provided
    seed_track = None
    selected_dimensions = None
    if request.seed_track:
        seed_track = await asyncio.to_thread(
            plex_client.get_track_by_key, request.seed_track.rating_key
        )
        if not seed_track:
            raise HTTPException(status_code=404, detail="Seed track not found")
        selected_dimensions = request.seed_track.selected_dimensions

    def event_stream():
        yield from generate_playlist_stream(
            prompt=request.prompt,
            seed_track=seed_track,
            selected_dimensions=selected_dimensions,
            additional_notes=request.additional_notes,
            genres=request.genres,
            decades=request.decades,
            track_count=request.track_count,
            exclude_live=request.exclude_live,
            min_rating=request.min_rating,
            max_tracks_to_ai=request.max_tracks_to_ai,
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# =============================================================================
# Playlist Endpoints
# =============================================================================


@app.post("/api/playlist", response_model=SavePlaylistResponse)
async def save_playlist(request: SavePlaylistRequest) -> SavePlaylistResponse:
    """Save a playlist to Plex."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    result = await asyncio.to_thread(
        plex_client.create_playlist, request.name, request.rating_keys
    )
    return SavePlaylistResponse(**result)


# =============================================================================
# Album Art Proxy
# =============================================================================


@app.get("/api/art/{rating_key}")
async def get_album_art(rating_key: str):
    """Proxy album art from Plex to avoid exposing token to browser."""
    if not rating_key.isdigit():
        raise HTTPException(status_code=400, detail="Invalid rating key format")

    plex_client = get_plex_client()
    config = get_config()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    # Get track to find thumb URL
    track = await asyncio.to_thread(plex_client.get_track_by_key, rating_key)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Get raw thumb path from Plex
    thumb_path = await asyncio.to_thread(plex_client.get_thumb_path, rating_key)
    if thumb_path:
        try:
            thumb_url = f"{config.plex.url}{thumb_path}"
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    thumb_url,
                    headers={"X-Plex-Token": config.plex.token},
                    timeout=10.0,
                )
                if response.status_code == 200:
                    return Response(
                        content=response.content,
                        media_type=response.headers.get("content-type", "image/jpeg"),
                    )
        except Exception:
            pass

    raise HTTPException(status_code=404, detail="Art not available")


# =============================================================================
# Static File Serving
# =============================================================================


# Determine the frontend directory path
# In development: ./frontend relative to repo root
# In Docker: /app/frontend
frontend_path = Path(__file__).parent.parent / "frontend"
if not frontend_path.exists():
    frontend_path = Path("/app/frontend")


# Mount static files if frontend directory exists
if frontend_path.exists():
    app.mount(
        "/static",
        StaticFiles(directory=frontend_path),
        name="static",
    )


@app.get("/")
async def serve_index():
    """Serve the main index.html page."""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "MediaSage API is running. Frontend not found."}
