"""FastAPI application for PlexSage."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx

from backend.config import get_config, update_config_values
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
    LibraryStatsResponse,
    GenreCount,
    DecadeCount,
    SavePlaylistRequest,
    SavePlaylistResponse,
    Track,
    UpdateConfigRequest,
)
from backend.plex_client import get_plex_client, init_plex_client
from backend.llm_client import get_llm_client, init_llm_client, get_max_tracks_for_model
from backend.analyzer import analyze_prompt as do_analyze_prompt, analyze_track as do_analyze_track
from backend.generator import generate_playlist as do_generate_playlist


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
    if config.llm.api_key:
        init_llm_client(config.llm)

    yield


app = FastAPI(
    title="PlexSage",
    description="Plex playlist generator powered by LLMs",
    version="0.1.0",
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

    # Check if LLM is configured
    llm_configured = bool(config.llm.api_key)

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
    max_tracks = get_max_tracks_for_model(generation_model)

    return ConfigResponse(
        plex_url=config.plex.url,
        plex_connected=plex_client.is_connected() if plex_client else False,
        plex_token_set=bool(config.plex.token),
        music_library=config.plex.music_library,
        llm_provider=config.llm.provider,
        llm_configured=bool(config.llm.api_key),
        llm_api_key_set=bool(config.llm.api_key),
        model_analysis=analysis_model,
        model_generation=generation_model,
        max_tracks_to_ai=max_tracks,
        defaults=config.defaults,
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

    if any(k in updates for k in ["llm_provider", "llm_api_key", "model_analysis", "model_generation"]):
        init_llm_client(config.llm)

    plex_client = get_plex_client()

    # Calculate recommended max tracks based on generation model
    generation_model = config.llm.model_generation
    analysis_model = config.llm.model_analysis
    max_tracks = get_max_tracks_for_model(generation_model)

    return ConfigResponse(
        plex_url=config.plex.url,
        plex_connected=plex_client.is_connected() if plex_client else False,
        plex_token_set=bool(config.plex.token),
        music_library=config.plex.music_library,
        llm_provider=config.llm.provider,
        llm_configured=bool(config.llm.api_key),
        llm_api_key_set=bool(config.llm.api_key),
        model_analysis=analysis_model,
        model_generation=generation_model,
        max_tracks_to_ai=max_tracks,
        defaults=config.defaults,
    )


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
    """Preview filter results with track count and cost estimate."""
    plex_client = get_plex_client()
    config = get_config()

    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    # Get filtered track count
    matching_tracks = await asyncio.to_thread(
        plex_client.get_filtered_track_count,
        genres=request.genres if request.genres else None,
        decades=request.decades if request.decades else None,
        min_rating=request.min_rating,
    )

    # Calculate how many tracks will actually be sent to AI
    if request.max_tracks_to_ai == 0:  # No limit
        tracks_to_send = matching_tracks if matching_tracks > 0 else 0
    else:
        tracks_to_send = min(matching_tracks, request.max_tracks_to_ai) if matching_tracks > 0 else 0

    # Estimate tokens for the generation request
    # Rough estimates: ~50 tokens per track in context, ~30 tokens per track in output
    estimated_input_tokens = 500 + (tracks_to_send * 50)  # Base prompt + track list
    estimated_output_tokens = request.track_count * 30  # ~30 tokens per track suggestion

    # Calculate cost based on provider
    # Claude Haiku: $0.25/1M input, $1.25/1M output
    # GPT-4.1-mini: $0.40/1M input, $1.60/1M output
    if config.llm.provider == "anthropic":
        input_cost_per_m = 0.25
        output_cost_per_m = 1.25
    else:
        input_cost_per_m = 0.40
        output_cost_per_m = 1.60

    estimated_cost = (
        (estimated_input_tokens / 1_000_000) * input_cost_per_m +
        (estimated_output_tokens / 1_000_000) * output_cost_per_m
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
    return {"message": "PlexSage API is running. Frontend not found."}
