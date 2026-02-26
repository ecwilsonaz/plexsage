"""FastAPI application for MediaSage."""

import asyncio
import json
import logging
import os
import random
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from starlette.responses import StreamingResponse
import httpx

from backend.config import get_config, get_current_media_client, update_config_values, load_user_yaml_config, save_user_config, ConfigSaveError
from backend.version import get_version
from backend.models import (
    AlbumCandidate,
    AlbumPreviewResponse,
    AnalyzePromptFiltersRequest,
    AnalyzePromptFiltersResponse,
    AnalyzePromptRequest,
    AnalyzePromptResponse,
    AnalyzeTrackRequest,
    AnalyzeTrackResponse,
    ConfigResponse,
    DecadeCount,
    FilterPreviewRequest,
    FilterPreviewResponse,
    GenerateRequest,
    GenreCount,
    HealthResponse,
    LibraryCacheStatusResponse,
    LibraryStatsResponse,
    OllamaModelInfo,
    OllamaModelsResponse,
    OllamaStatus,
    PlexClientInfo,
    PlexPlaylistInfo,
    PlayQueueRequest,
    PlayQueueResponse,
    RecommendGenerateRequest,
    RecommendGenerateResponse,
    RecommendQuestionsRequest,
    RecommendQuestionsResponse,
    RecommendSessionState,
    RecommendSwitchModeRequest,
    RecommendSwitchModeResponse,
    ResultDetail,
    ResultListItem,
    ResultListResponse,
    SavePlaylistRequest,
    SavePlaylistResponse,
    SetupCompleteResponse,
    SetupStatusResponse,
    SyncProgress,
    SyncTriggerResponse,
    Track,
    UpdateConfigRequest,
    UpdatePlaylistRequest,
    UpdatePlaylistResponse,
    ValidateAIRequest,
    ValidateAIResponse,
    ValidateJellyfinRequest,
    ValidateJellyfinResponse,
    ValidatePlexRequest,
    ValidatePlexResponse,
    album_key,
)
from backend.plex_client import PlexClient as PlexClientInstance, get_plex_client, init_plex_client
from backend.jellyfin_client import JellyfinClient, get_jellyfin_client, init_jellyfin_client
from backend import library_cache
from backend.llm_client import (
    TOKENS_PER_ALBUM,
    estimate_cost_for_model,
    get_llm_client,
    get_max_albums_for_model,
    get_max_tracks_for_model,
    get_model_cost,
    get_ollama_model_info,
    get_ollama_status,
    init_llm_client,
    list_ollama_models,
)
from backend.analyzer import analyze_prompt as do_analyze_prompt, analyze_track as do_analyze_track
from backend.generator import generate_playlist_stream

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    # Initialize Jellyfin client if configured
    if config.jellyfin.url and config.jellyfin.token:
        init_jellyfin_client(
            config.jellyfin.url,
            config.jellyfin.token,
            config.jellyfin.music_library,
        )

    # Initialize LLM client if configured
    # Local providers (ollama, custom) don't need an API key
    if config.llm.api_key or config.llm.provider in ("ollama", "custom"):
        init_llm_client(config.llm)

    # Initialize DB schema early so migration flag is set
    library_cache.ensure_db_initialized().close()

    # Auto-sync if a migration was applied and existing tracks need re-sync
    _media_client = get_current_media_client()
    if library_cache.needs_resync() and _media_client and _media_client.is_connected():
        logger.info("Schema migration detected — starting automatic library re-sync")

        async def _run_resync():
            try:
                await asyncio.to_thread(library_cache.sync_library, _media_client)
            except Exception as e:
                logger.error("Auto-resync failed: %s", e)

        asyncio.create_task(_run_resync())

    yield

    # Shutdown: clean up resources
    if _music_research_client is not None:
        await _music_research_client.close()
    if _art_proxy_client is not None:
        await _art_proxy_client.aclose()


app = FastAPI(
    title="MediaSage",
    description="Plex playlist generator powered by LLMs",
    version=get_version(),
    lifespan=lifespan,
)


# =============================================================================
# Config Helpers
# =============================================================================


def _is_llm_configured(config) -> bool:
    """Check if an LLM provider is configured (API key for cloud, URL for local)."""
    if config.llm.provider == "ollama" and config.llm.ollama_url:
        return True
    if config.llm.provider == "custom" and config.llm.custom_url:
        return True
    return bool(config.llm.api_key)


def _build_config_response(config, plex_client) -> ConfigResponse:
    """Build a ConfigResponse from the current config and Plex client state."""
    generation_model = config.llm.model_generation
    analysis_model = config.llm.model_analysis
    max_tracks = get_max_tracks_for_model(generation_model, config=config.llm)
    max_albums = get_max_albums_for_model(generation_model, config=config.llm)

    is_local = config.llm.provider in ("ollama", "custom")
    gen_costs = get_model_cost(generation_model, config.llm)
    analysis_costs = get_model_cost(analysis_model, config.llm)

    # Determine music_library based on active media server
    if config.media_server == "jellyfin":
        active_music_library = config.jellyfin.music_library
    else:
        active_music_library = config.plex.music_library

    return ConfigResponse(
        version=get_version(),
        media_server=config.media_server,
        plex_url=config.plex.url,
        plex_connected=plex_client.is_connected() if plex_client else False,
        plex_token_set=bool(config.plex.token),
        music_library=active_music_library,
        jellyfin_url=config.jellyfin.url,
        jellyfin_token_set=bool(config.jellyfin.token),
        jellyfin_music_library=config.jellyfin.music_library,
        llm_provider=config.llm.provider,
        llm_configured=_is_llm_configured(config),
        llm_api_key_set=bool(config.llm.api_key),
        model_analysis=analysis_model,
        model_generation=generation_model,
        max_tracks_to_ai=max_tracks,
        max_albums_to_ai=max_albums,
        cost_per_million_input=gen_costs["input"],
        cost_per_million_output=gen_costs["output"],
        analysis_cost_per_million_input=analysis_costs["input"],
        analysis_cost_per_million_output=analysis_costs["output"],
        defaults=config.defaults,
        ollama_url=config.llm.ollama_url,
        ollama_context_window=config.llm.ollama_context_window,
        custom_url=config.llm.custom_url,
        custom_context_window=config.llm.custom_context_window,
        is_local_provider=is_local,
        provider_from_env=os.environ.get("LLM_PROVIDER") is not None,
    )


# =============================================================================
# Health Endpoint
# =============================================================================


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check application health status."""
    config = get_config()
    media_client = get_current_media_client()

    return HealthResponse(
        status="healthy",
        plex_connected=media_client.is_connected() if media_client else False,
        llm_configured=_is_llm_configured(config),
        media_server=config.media_server,
    )


# =============================================================================
# Setup/Onboarding Endpoints
# =============================================================================


@app.get("/api/setup/status", response_model=SetupStatusResponse)
async def setup_status() -> SetupStatusResponse:
    """Get onboarding checklist state for the setup wizard."""
    config = get_config()
    plex_client = get_plex_client()
    jellyfin_client = get_jellyfin_client()

    # Check data dir writable by actually creating+deleting a temp file
    # (more reliable than os.access for Docker bind mounts)
    data_dir = library_cache.DATA_DIR
    data_dir_writable = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        data_dir_writable = True
    except OSError:
        pass

    # Plex status
    plex_connected = plex_client.is_connected() if plex_client else False
    plex_error = plex_client.get_error() if plex_client and not plex_connected else None

    # Jellyfin status
    jellyfin_connected = jellyfin_client.is_connected() if jellyfin_client else False
    jellyfin_error = jellyfin_client.get_error() if jellyfin_client and not jellyfin_connected else None

    # Music libraries from active server
    if config.media_server == "jellyfin" and jellyfin_connected and jellyfin_client:
        music_libraries = jellyfin_client.get_music_libraries()
    elif plex_connected and plex_client:
        music_libraries = plex_client.get_music_libraries()
    else:
        music_libraries = []

    # LLM status
    llm_configured = _is_llm_configured(config)
    llm_from_env = any(
        os.environ.get(k)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_URL", "CUSTOM_LLM_URL")
    )

    # Library cache status
    library_synced = library_cache.has_cached_tracks()
    sync_state = library_cache.get_sync_state()
    sync_progress = None
    if sync_state["sync_progress"]:
        sync_progress = SyncProgress(
            phase=sync_state["sync_progress"]["phase"],
            current=sync_state["sync_progress"]["current"],
            total=sync_state["sync_progress"]["total"],
        )

    # Setup complete flag
    user_config = load_user_yaml_config()
    setup_complete = user_config.get("setup", {}).get("complete", False)

    return SetupStatusResponse(
        data_dir_writable=data_dir_writable,
        process_uid=getattr(os, "getuid", lambda: 0)(),
        process_gid=getattr(os, "getgid", lambda: 0)(),
        data_dir=str(data_dir),
        media_server=config.media_server,
        plex_connected=plex_connected,
        plex_error=plex_error,
        plex_from_env=bool(os.environ.get("PLEX_URL")),
        jellyfin_connected=jellyfin_connected,
        jellyfin_error=jellyfin_error,
        jellyfin_from_env=bool(os.environ.get("JELLYFIN_URL")),
        music_libraries=music_libraries,
        llm_configured=llm_configured,
        llm_provider=config.llm.provider,
        llm_from_env=llm_from_env,
        library_synced=library_synced,
        track_count=sync_state["track_count"],
        is_syncing=sync_state["is_syncing"],
        sync_progress=sync_progress,
        setup_complete=setup_complete,
    )


@app.post("/api/setup/validate-plex", response_model=ValidatePlexResponse)
async def setup_validate_plex(request: ValidatePlexRequest) -> ValidatePlexResponse:
    """Validate Plex credentials and save on success."""
    try:
        temp_client = await asyncio.to_thread(
            PlexClientInstance, request.plex_url, request.plex_token, request.music_library
        )
    except Exception as e:
        return ValidatePlexResponse(success=False, error=str(e))

    if not temp_client.is_connected():
        return ValidatePlexResponse(
            success=False,
            error=temp_client.get_error() or "Connection failed",
        )

    # Connected — save config and reinitialize global client
    music_libraries = temp_client.get_music_libraries()
    server_name = None
    if temp_client._server:
        server_name = temp_client._server.friendlyName

    try:
        update_config_values({
            "plex_url": request.plex_url,
            "plex_token": request.plex_token,
            "music_library": request.music_library,
        })
    except ConfigSaveError as e:
        return ValidatePlexResponse(success=False, error=str(e))

    init_plex_client(request.plex_url, request.plex_token, request.music_library)

    return ValidatePlexResponse(
        success=True,
        server_name=server_name,
        music_libraries=music_libraries,
    )


@app.post("/api/setup/validate-jellyfin", response_model=ValidateJellyfinResponse)
async def setup_validate_jellyfin(request: ValidateJellyfinRequest) -> ValidateJellyfinResponse:
    """Validate Jellyfin credentials and save on success."""
    try:
        temp_client = await asyncio.to_thread(
            JellyfinClient, request.jellyfin_url, request.jellyfin_token, request.music_library
        )
    except Exception as e:
        return ValidateJellyfinResponse(success=False, error=str(e))

    if not temp_client.is_connected():
        return ValidateJellyfinResponse(
            success=False,
            error=temp_client.get_error() or "Connection failed",
        )

    music_libraries = temp_client.get_music_libraries()
    server_name = temp_client.get_server_name()
    user_id = temp_client._user_id

    try:
        update_config_values({
            "media_server": "jellyfin",
            "jellyfin_url": request.jellyfin_url,
            "jellyfin_token": request.jellyfin_token,
            "jellyfin_music_library": request.music_library,
        })
    except ConfigSaveError as e:
        return ValidateJellyfinResponse(success=False, error=str(e))

    init_jellyfin_client(request.jellyfin_url, request.jellyfin_token, request.music_library)

    return ValidateJellyfinResponse(
        success=True,
        server_name=server_name,
        music_libraries=music_libraries,
        user_id=user_id,
    )


@app.post("/api/setup/validate-ai", response_model=ValidateAIResponse)
async def setup_validate_ai(request: ValidateAIRequest) -> ValidateAIResponse:
    """Validate AI provider credentials and save on success."""
    provider = request.provider
    provider_name = {
        "anthropic": "Anthropic (Claude)",
        "openai": "OpenAI (GPT)",
        "gemini": "Google (Gemini)",
        "ollama": "Ollama (Local)",
        "custom": "Custom (OpenAI-compatible)",
    }.get(provider, provider)

    try:
        if provider == "gemini":
            import google.genai as genai
            client = genai.Client(api_key=request.api_key)
            await asyncio.to_thread(lambda: list(client.models.list()))

        elif provider == "openai":
            import openai
            client = openai.OpenAI(api_key=request.api_key)
            await asyncio.to_thread(lambda: list(client.models.list()))

        elif provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=request.api_key)
            await asyncio.to_thread(
                client.messages.create,
                model="claude-haiku-4-5",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )

        elif provider == "ollama":
            status = await asyncio.to_thread(get_ollama_status, request.ollama_url or "http://localhost:11434")
            if not status.connected:
                return ValidateAIResponse(
                    success=False,
                    error=status.error or "Cannot connect to Ollama",
                    provider_name=provider_name,
                )

        elif provider == "custom":
            if not request.custom_url:
                return ValidateAIResponse(
                    success=False, error="Custom URL is required", provider_name=provider_name
                )
            headers = {}
            if request.api_key:
                headers["Authorization"] = f"Bearer {request.api_key}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{request.custom_url.rstrip('/')}/models", headers=headers)
                resp.raise_for_status()

        else:
            return ValidateAIResponse(
                success=False, error=f"Unknown provider: {provider}", provider_name=provider_name
            )

    except Exception as e:
        error_msg = str(e)
        # Provide friendlier messages for common errors
        if "401" in error_msg or "Unauthorized" in error_msg or "AuthenticationError" in error_msg:
            error_msg = "Invalid API key"
        elif "Could not resolve" in error_msg or "Connection" in error_msg.lower():
            error_msg = f"Cannot connect to {provider_name}"
        return ValidateAIResponse(success=False, error=error_msg, provider_name=provider_name)

    # Save config
    config_updates = {"llm_provider": provider}
    if request.api_key:
        config_updates["llm_api_key"] = request.api_key
    if provider == "ollama" and request.ollama_url:
        config_updates["ollama_url"] = request.ollama_url
    if provider == "custom" and request.custom_url:
        config_updates["custom_url"] = request.custom_url

    try:
        config = update_config_values(config_updates)
        init_llm_client(config.llm)
    except ConfigSaveError as e:
        return ValidateAIResponse(success=False, error=str(e), provider_name=provider_name)

    return ValidateAIResponse(success=True, provider_name=provider_name)


@app.post("/api/setup/complete", response_model=SetupCompleteResponse)
async def setup_complete() -> SetupCompleteResponse:
    """Mark onboarding as complete."""
    try:
        save_user_config({"setup": {"complete": True}})
    except Exception as e:
        logger.warning("Failed to save setup complete flag: %s", e)
    return SetupCompleteResponse(success=True)


# =============================================================================
# Configuration Endpoints
# =============================================================================


@app.get("/api/config", response_model=ConfigResponse)
async def get_configuration() -> ConfigResponse:
    """Get current configuration (without secrets)."""
    return _build_config_response(get_config(), get_plex_client())


@app.post("/api/config", response_model=ConfigResponse)
async def update_configuration(request: UpdateConfigRequest) -> ConfigResponse:
    """Update configuration values."""
    updates = {
        k: v
        for k, v in request.model_dump().items()
        if v is not None
    }

    if not updates:
        raise HTTPException(status_code=400, detail="No configuration values provided")

    try:
        config = update_config_values(updates)
    except ConfigSaveError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Reinitialize clients if relevant config changed
    if any(k in updates for k in ["plex_url", "plex_token", "music_library"]):
        init_plex_client(
            config.plex.url,
            config.plex.token,
            config.plex.music_library,
        )

    if any(k in updates for k in ["jellyfin_url", "jellyfin_token", "jellyfin_music_library"]):
        if config.jellyfin.url and config.jellyfin.token:
            init_jellyfin_client(
                config.jellyfin.url,
                config.jellyfin.token,
                config.jellyfin.music_library,
            )

    if any(k in updates for k in ["llm_provider", "llm_api_key", "model_analysis", "model_generation", "ollama_url", "custom_url"]):
        init_llm_client(config.llm)

    return _build_config_response(config, get_plex_client())


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
    media_client = get_current_media_client()

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
        plex_connected=media_client.is_connected() if media_client else False,
        needs_resync=library_cache.needs_resync(),
    )


@app.post("/api/library/sync", response_model=SyncTriggerResponse)
async def trigger_library_sync() -> SyncTriggerResponse:
    """Trigger library sync from the configured media server.

    Always starts sync in background so progress can be polled.
    """
    media_client = get_current_media_client()
    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")

    # Check if already syncing
    progress = library_cache.get_sync_progress()
    if progress["is_syncing"]:
        raise HTTPException(status_code=409, detail="Sync already in progress")

    # Always run sync in background so progress can be polled
    asyncio.create_task(
        asyncio.to_thread(library_cache.sync_library, media_client)
    )
    return SyncTriggerResponse(started=True, blocking=False)


# =============================================================================
# Library Endpoints
# =============================================================================


@app.get("/api/library/stats", response_model=LibraryStatsResponse)
async def get_library_stats() -> LibraryStatsResponse:
    """Get library statistics."""
    media_client = get_current_media_client()
    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")

    stats = await asyncio.to_thread(media_client.get_library_stats)
    return LibraryStatsResponse(
        total_tracks=stats.get("total_tracks", 0),
        genres=[GenreCount(**g) for g in stats.get("genres", [])],
        decades=[DecadeCount(**d) for d in stats.get("decades", [])],
    )


@app.get("/api/library/stats/cached", response_model=LibraryStatsResponse)
async def get_library_stats_cached() -> LibraryStatsResponse:
    """Get genre/decade stats from the local cache (no Plex round-trip)."""
    stats = await asyncio.to_thread(library_cache.get_cached_genre_decade_stats)
    return LibraryStatsResponse(
        total_tracks=0,  # Not needed for filter chips
        genres=[GenreCount(**g) for g in stats["genres"]],
        decades=[DecadeCount(**d) for d in stats["decades"]],
    )


@app.get("/api/library/search", response_model=list[Track])
async def search_library(q: str = Query(..., description="Search query")) -> list[Track]:
    """Search for tracks in the library."""
    media_client = get_current_media_client()
    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")

    # Normalize smart/curly quotes to straight quotes (iOS auto-correction)
    normalized = q.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return await asyncio.to_thread(media_client.search_tracks, normalized)


# =============================================================================
# Analysis Endpoints
# =============================================================================


@app.post("/api/analyze/prompt", response_model=AnalyzePromptResponse)
async def analyze_prompt(request: AnalyzePromptRequest) -> AnalyzePromptResponse:
    """Analyze a natural language prompt to suggest filters."""
    media_client = get_current_media_client()
    llm_client = get_llm_client()

    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")
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
    media_client = get_current_media_client()
    llm_client = get_llm_client()

    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # Get the track
    track = await asyncio.to_thread(media_client.get_track_by_key, request.rating_key)
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

    # Fall back to media server if cache is empty
    if matching_tracks < 0:
        media_client = get_current_media_client()
        if not media_client or not media_client.is_connected():
            raise HTTPException(status_code=503, detail="Media server not connected")

        matching_tracks = await asyncio.to_thread(
            media_client.count_tracks_by_filters,
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

    # Estimate tokens for all 3 API calls based on real-world testing (Feb 2026):
    # - Analysis call (model_analysis): ~700 input, ~100 output
    # - Generation call (model_generation): ~(tracks * 40) input, ~(track_count * 60) output
    # - Narrative call (model_analysis): ~400 input, ~200 output
    analysis_input = 1100  # analysis (700) + narrative (400)
    analysis_output = 300  # analysis (100) + narrative (200)
    generation_input = tracks_to_send * 40
    generation_output = request.track_count * 60  # ~60 tokens per track in response

    estimated_input_tokens = analysis_input + generation_input
    estimated_output_tokens = analysis_output + generation_output

    # Calculate cost separately for each model since they may have different pricing
    analysis_cost = estimate_cost_for_model(
        config.llm.model_analysis,
        analysis_input,
        analysis_output,
        config=config.llm,
    )
    generation_cost = estimate_cost_for_model(
        config.llm.model_generation,
        generation_input,
        generation_output,
        config=config.llm,
    )
    estimated_cost = analysis_cost + generation_cost

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


@app.post("/api/generate/stream")
async def generate_playlist_sse(request: GenerateRequest) -> StreamingResponse:
    """Generate a playlist with streaming progress updates."""
    media_client = get_current_media_client()
    llm_client = get_llm_client()

    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM not configured")

    # Get seed track if provided
    seed_track = None
    selected_dimensions = None
    if request.seed_track:
        seed_track = await asyncio.to_thread(
            media_client.get_track_by_key, request.seed_track.rating_key
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
            refinement_answers=request.refinement_answers,
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
            "X-Accel-Buffering": "no",  # Disable buffering for nginx/reverse proxies
        },
    )


# =============================================================================
# Playlist Endpoints
# =============================================================================


@app.post("/api/playlist", response_model=SavePlaylistResponse)
async def save_playlist(request: SavePlaylistRequest) -> SavePlaylistResponse:
    """Save a playlist to the configured media server."""
    media_client = get_current_media_client()
    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")

    result = await asyncio.to_thread(
        media_client.create_playlist,
        request.name,
        request.rating_keys,
        request.description,
    )
    return SavePlaylistResponse(**result)


# =============================================================================
# Instant Queue Endpoints
# =============================================================================


@app.get("/api/plex/clients", response_model=list[PlexClientInfo])
async def get_plex_clients() -> list[PlexClientInfo]:
    """List online Plex clients capable of playback."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    return await asyncio.to_thread(plex_client.get_clients)


@app.post("/api/play-queue", response_model=PlayQueueResponse)
async def create_play_queue(request: PlayQueueRequest) -> PlayQueueResponse:
    """Create a play queue and start playback on a Plex client."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    result = await asyncio.to_thread(
        plex_client.play_queue,
        request.rating_keys,
        request.client_id,
        request.mode,
    )

    if not result["success"]:
        if result.get("error_code") == "not_found":
            raise HTTPException(status_code=404, detail=result["error"])
        raise HTTPException(status_code=500, detail=result.get("error", "Play queue creation failed"))

    return PlayQueueResponse(**result)


@app.get("/api/plex/playlists", response_model=list[PlexPlaylistInfo])
async def get_plex_playlists() -> list[PlexPlaylistInfo]:
    """List audio playlists on the Plex server."""
    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    return await asyncio.to_thread(plex_client.get_playlists)


@app.get("/api/jellyfin/playlists", response_model=list[PlexPlaylistInfo])
async def get_jellyfin_playlists() -> list[PlexPlaylistInfo]:
    """List audio playlists on the Jellyfin server."""
    jellyfin_client = get_jellyfin_client()
    if not jellyfin_client or not jellyfin_client.is_connected():
        raise HTTPException(status_code=503, detail="Jellyfin not connected")

    return await asyncio.to_thread(jellyfin_client.get_playlists)


@app.post("/api/playlist/update", response_model=UpdatePlaylistResponse)
async def update_playlist(request: UpdatePlaylistRequest) -> UpdatePlaylistResponse:
    """Update an existing playlist by replacing or appending tracks."""
    media_client = get_current_media_client()
    if not media_client or not media_client.is_connected():
        raise HTTPException(status_code=503, detail="Media server not connected")

    result = await asyncio.to_thread(
        media_client.update_playlist,
        request.playlist_id,
        request.rating_keys,
        request.mode,
        request.description,
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Playlist update failed"))

    return UpdatePlaylistResponse(**result)


# =============================================================================
# Recommendation Endpoints (006)
# =============================================================================

# Module-level pipeline instance (initialized lazily)
_recommendation_pipeline = None
_recommendation_pipeline_llm = None  # Track which LLM client the pipeline was built with
_music_research_client = None
_art_proxy_client: httpx.AsyncClient | None = None
_art_proxy_lock = asyncio.Lock()
_pipeline_lock = threading.Lock()
_research_client_lock = threading.Lock()


def _get_pipeline():
    """Get or create the recommendation pipeline. Recreates if LLM client changed."""
    global _recommendation_pipeline, _recommendation_pipeline_llm
    llm_client = get_llm_client()
    if llm_client is None:
        return None
    if _recommendation_pipeline is None or _recommendation_pipeline_llm is not llm_client:
        with _pipeline_lock:
            # Double-check inside lock
            if _recommendation_pipeline is None or _recommendation_pipeline_llm is not llm_client:
                from backend.recommender import RecommendationPipeline
                config = get_config()
                old_pipeline = _recommendation_pipeline
                _recommendation_pipeline = RecommendationPipeline(config, llm_client)
                # Migrate active sessions from old pipeline to preserve in-flight requests
                if old_pipeline is not None:
                    _recommendation_pipeline.migrate_sessions_from(old_pipeline)
                _recommendation_pipeline_llm = llm_client
    return _recommendation_pipeline


def _get_research_client():
    """Get or create the music research client."""
    global _music_research_client
    if _music_research_client is None:
        with _research_client_lock:
            if _music_research_client is None:
                from backend.music_research import MusicResearchClient
                _music_research_client = MusicResearchClient()
    return _music_research_client


async def _get_art_proxy_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client for art proxying."""
    global _art_proxy_client
    if _art_proxy_client is None or _art_proxy_client.is_closed:
        async with _art_proxy_lock:
            if _art_proxy_client is None or _art_proxy_client.is_closed:
                _art_proxy_client = httpx.AsyncClient(timeout=10.0)
    return _art_proxy_client


async def _set_cover_art_from_research(rec, rd, research_client) -> None:
    """Fetch cover art from Cover Art Archive when rec has no art_url."""
    if not rec.art_url and rd.earliest_release_mbid:
        art_url = await research_client.fetch_cover_art(
            rd.earliest_release_mbid, release_group_mbid=rd.musicbrainz_id,
        )
        if art_url:
            rec.art_url = f"/api/external-art?url={quote(art_url, safe='')}"


def _apply_year_override(rec, rd):
    """Override rec.year with MusicBrainz release_date year when available."""
    if rd.release_date and len(rd.release_date) >= 4:
        try:
            mb_year = int(rd.release_date[:4])
            if rec.year != mb_year:
                logger.info(
                    "Year override: Plex=%s → MusicBrainz=%s for %s — %s",
                    rec.year, mb_year, rec.artist, rec.album,
                )
                rec.year = mb_year
        except ValueError:
            pass


@app.get("/api/recommend/albums/preview", response_model=AlbumPreviewResponse)
async def recommend_albums_preview(
    genres: str | None = Query(None, description="Comma-separated genre names"),
    decades: str | None = Query(None, description="Comma-separated decade names"),
    max_albums: int = Query(2500, description="Max albums to send to AI"),
) -> AlbumPreviewResponse:
    """Preview filtered album counts and cost estimates for recommendation."""
    genre_list = [g.strip() for g in genres.split(",") if g.strip()] if genres else None
    decade_list = [d.strip() for d in decades.split(",") if d.strip()] if decades else None

    # Get album count from cache
    if library_cache.has_cached_tracks():
        candidates = await asyncio.to_thread(
            library_cache.get_album_candidates,
            genres=genre_list,
            decades=decade_list,
        )
        matching_albums = len(candidates)
    else:
        matching_albums = 0

    albums_to_send = min(matching_albums, max_albums) if max_albums > 0 else matching_albums
    config = get_config()

    # Estimate tokens for up to 7 LLM calls using hardcoded empirical constants
    # Gap analysis: ~800 input, ~50 output (analysis model)
    # Question gen: ~600 input, ~200 output (generation model)
    # Album selection: albums * TOKENS_PER_ALBUM + ~400 input, ~300 output (generation model)
    # Pitch writing: ~1500 input, ~800 output (analysis model)
    # Fact extraction: ~2000 input, ~500 output (generation model) — research-dependent
    # Pitch validation: ~2000 input, ~200 output (analysis model) — research-dependent
    # Pitch rewrite: ~1500 input, ~800 output (analysis model) — only if validation fails
    analysis_input = 800 + 1500 + 2000 + 1500  # gap + pitch + validation + rewrite
    analysis_output = 50 + 800 + 200 + 800
    generation_input = 600 + (albums_to_send * TOKENS_PER_ALBUM) + 400 + 2000  # question + selection + extraction
    generation_output = 200 + 300 + 500

    estimated_input_tokens = analysis_input + generation_input

    analysis_cost = estimate_cost_for_model(
        config.llm.model_analysis, analysis_input, analysis_output, config=config.llm
    )
    generation_cost = estimate_cost_for_model(
        config.llm.model_generation, generation_input, generation_output, config=config.llm
    )
    estimated_cost = analysis_cost + generation_cost

    return AlbumPreviewResponse(
        matching_albums=matching_albums,
        albums_to_send=albums_to_send,
        estimated_input_tokens=estimated_input_tokens,
        estimated_cost=estimated_cost,
    )


@app.post("/api/recommend/analyze-prompt", response_model=AnalyzePromptFiltersResponse)
async def recommend_analyze_prompt(request: AnalyzePromptFiltersRequest) -> AnalyzePromptFiltersResponse:
    """Analyze a prompt and suggest relevant genre/decade filters."""
    pipeline = _get_pipeline()
    if not pipeline:
        # Fallback: return all available
        return AnalyzePromptFiltersResponse(
            genres=request.genres,
            decades=request.decades,
            reasoning="LLM not configured; returning all filters.",
        )

    try:
        result = await asyncio.to_thread(
            pipeline.analyze_prompt_filters,
            request.prompt,
            request.genres,
            request.decades,
        )
        return AnalyzePromptFiltersResponse(
            genres=result["genres"],
            decades=result["decades"],
            reasoning=result["reasoning"],
        )
    except Exception:
        logger.exception("analyze-prompt failed, returning all filters")
        return AnalyzePromptFiltersResponse(
            genres=request.genres,
            decades=request.decades,
            reasoning="Analysis failed; returning all filters.",
        )


@app.post("/api/recommend/questions", response_model=RecommendQuestionsResponse)
async def recommend_questions(request: RecommendQuestionsRequest) -> RecommendQuestionsResponse:
    """Generate clarifying questions for album recommendation."""
    pipeline = _get_pipeline()
    if not pipeline:
        raise HTTPException(status_code=503, detail="LLM not configured")

    try:
        # Create session upfront so gap analysis and question costs are tracked
        session_state = RecommendSessionState(
            mode="library",
            prompt=request.prompt,
            filters={"genres": [], "decades": []},
            questions=[],
            album_candidates=[],
            taste_profile=None,
            familiarity_pref="any",
        )
        session_id = pipeline.create_session(session_state)

        # Run gap analysis + question generation (only needs the prompt)
        dimension_ids = await asyncio.to_thread(
            pipeline.gap_analysis, request.prompt, session_id
        )
        questions = await asyncio.to_thread(
            pipeline.generate_questions, request.prompt, dimension_ids, session_id
        )

        # Store questions in session
        pipeline.update_session_questions(session_id, questions)

        total_tokens, total_cost = pipeline.get_session_costs(session_id)

        return RecommendQuestionsResponse(
            questions=questions,
            session_id=session_id,
            token_count=total_tokens,
            estimated_cost=total_cost,
        )
    except Exception as e:
        # Clean up the session if question generation failed
        if 'session_id' in locals():
            pipeline.delete_session(session_id)
        raise HTTPException(status_code=500, detail=f"Question generation failed: {str(e)}")


@app.post("/api/recommend/switch-mode", response_model=RecommendSwitchModeResponse)
async def recommend_switch_mode(request: RecommendSwitchModeRequest) -> RecommendSwitchModeResponse:
    """Switch a recommendation session to a different mode, keeping answers."""
    pipeline = _get_pipeline()
    if not pipeline:
        raise HTTPException(status_code=503, detail="LLM not configured")

    old_session = pipeline.get_session(request.session_id)
    if not old_session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    if request.mode == old_session.mode:
        return RecommendSwitchModeResponse(session_id=request.session_id)

    # Create new session with empty candidates — generate will load them
    new_session = RecommendSessionState(
        mode=request.mode,
        prompt=old_session.prompt,
        filters=old_session.filters,
        questions=old_session.questions,
        answers=old_session.answers,
        answer_texts=old_session.answer_texts,
        album_candidates=[],
        taste_profile=None,
        familiarity_pref=old_session.familiarity_pref,
        previously_recommended=old_session.previously_recommended,
    )
    new_session_id = pipeline.create_session(new_session)
    pipeline.delete_session(request.session_id)  # Clean up old session

    return RecommendSwitchModeResponse(session_id=new_session_id)


@app.post("/api/recommend/generate")
async def recommend_generate(request: RecommendGenerateRequest, raw_request: Request) -> StreamingResponse:
    """Generate album recommendations with SSE progress streaming."""
    pipeline = _get_pipeline()
    if not pipeline:
        raise HTTPException(status_code=503, detail="LLM not configured")

    session = pipeline.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # Store answers in session (thread-safe)
    pipeline.update_session_answers(
        request.session_id, request.answers, request.answer_texts
    )

    # Load album candidates and build state before locked session update
    genre_list = request.genres if request.genres else None
    decade_list = request.decades if request.decades else None
    loaded_candidates = None
    loaded_taste_profile = None

    if not library_cache.has_cached_tracks():
        if request.mode == "library":
            raise HTTPException(status_code=400, detail="Library cache is empty. Please sync your library first.")
        elif request.mode == "discovery":
            raise HTTPException(status_code=400, detail="Library cache is empty. Discovery mode needs your library to build a taste profile. Please sync first.")
    else:
        candidates_raw = await asyncio.to_thread(
            library_cache.get_album_candidates,
            genres=genre_list if request.mode == "library" else None,
            decades=decade_list if request.mode == "library" else None,
        )

        if request.mode == "library" and not candidates_raw:
            if library_cache.has_cached_tracks():
                sync_state = library_cache.get_sync_state()
                if sync_state["is_syncing"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Library sync in progress. Album recommendations will be available once it completes.",
                    )
                raise HTTPException(
                    status_code=400,
                    detail="Your library needs a fresh sync to enable album recommendations. Please re-sync from Settings or the footer Refresh link.",
                )
            raise HTTPException(status_code=400, detail="No albums match your filters. Try broadening your genre or decade selection.")

        loaded_candidates = [AlbumCandidate(**c) for c in candidates_raw]

        # Cap albums sent to AI based on user-selected limit (random sample for unbiased selection)
        if request.max_albums > 0 and len(loaded_candidates) > request.max_albums:
            loaded_candidates = random.sample(loaded_candidates, request.max_albums)

        # Build taste profile for discovery mode
        if request.mode == "discovery":
            all_raw = await asyncio.to_thread(
                library_cache.get_album_candidates, genres=None, decades=None
            )
            all_candidates = [AlbumCandidate(**c) for c in all_raw]
            loaded_taste_profile = pipeline.build_taste_profile(all_candidates)

    # Thread-safe session state update (all fields updated atomically under lock)
    pipeline.update_session_generate_state(
        request.session_id,
        mode=request.mode,
        filters={"genres": request.genres, "decades": request.decades},
        familiarity_pref=request.familiarity_pref,
        album_candidates=loaded_candidates,
        taste_profile=loaded_taste_profile,
    )

    # Snapshot fields before entering the generator to avoid TOCTOU races —
    # a concurrent "Show me another" request could mutate the session while
    # event_stream is still reading from it. Use request fields directly
    # where available (immutable per-request); only read session for
    # accumulated state (prompt set during questions, previously_recommended).
    _prompt = session.prompt
    _answers = list(request.answers) if request.answers else []
    _answer_texts = list(request.answer_texts) if request.answer_texts else []
    _familiarity_pref = request.familiarity_pref
    _previously_recommended = list(session.previously_recommended) if session.previously_recommended else None

    async def event_stream():
        research_warning = None
        research_data = {}

        # iOS Safari aggressively suspends background tabs, tearing down TCP
        # connections even when the user intends to return. Skip server-side
        # disconnect checks for iOS to avoid false-positive aborts.
        _ua = (raw_request.headers.get("user-agent") or "").lower()
        _is_ios = "iphone" in _ua or "ipad" in _ua

        async def _check_disconnect():
            """Abort if the client has disconnected (saves LLM token costs)."""
            if _is_ios:
                return False
            if await raw_request.is_disconnected():
                logger.info("Client disconnected, aborting recommendation for session %s", request.session_id)
                return True
            return False

        try:
            is_discovery = request.mode == "discovery"
            selecting_msg = "Finding albums to recommend..." if is_discovery else "Choosing albums from your library..."

            # Step 1: Select albums
            yield f"event: progress\ndata: {json.dumps({'step': 'selecting', 'message': selecting_msg})}\n\n"

            # Query familiarity from cache if pref is not "any" (library mode only)
            familiarity_data = None
            if request.familiarity_pref != "any" and not is_discovery:
                try:
                    candidate_keys = [c.parent_rating_key for c in loaded_candidates if c.parent_rating_key]
                    if candidate_keys:
                        familiarity_data = await asyncio.to_thread(
                            library_cache.get_album_familiarity, candidate_keys
                        )
                except Exception as e:
                    logger.warning("Familiarity query failed: %s", e)

            if is_discovery:
                if not loaded_taste_profile:
                    raise ValueError(
                        "Discovery mode requires a library profile. "
                        "Please sync your library and start a new recommendation."
                    )
                recommendations = await asyncio.to_thread(
                    pipeline.select_discovery_albums,
                    prompt=_prompt,
                    answers=_answers,
                    answer_texts=_answer_texts,
                    taste_profile=loaded_taste_profile,
                    session_id=request.session_id,
                    previously_recommended=_previously_recommended,
                    max_exclusion_albums=request.max_albums if request.max_albums > 0 else 2500,
                )
            else:
                recommendations = await asyncio.to_thread(
                    pipeline.select_albums,
                    prompt=_prompt,
                    answers=_answers,
                    answer_texts=_answer_texts,
                    album_candidates=loaded_candidates,
                    session_id=request.session_id,
                    familiarity_pref=request.familiarity_pref,
                    familiarity_data=familiarity_data,
                    previously_recommended=_previously_recommended,
                )

            if not recommendations:
                raise ValueError(
                    "No matching albums found. "
                    "Try broadening your prompt or adjusting filters."
                )

            # Step 2: Research primary album
            if await _check_disconnect():
                return
            yield f"event: progress\ndata: {json.dumps({'step': 'researching_primary', 'message': 'Researching an album...'})}\n\n"

            research_client = _get_research_client()
            primary = next((r for r in recommendations if r.rank == "primary"), None)
            if primary:
                try:
                    rd = await research_client.research_album(primary.artist, primary.album, full=True, year=primary.year)
                    if rd.musicbrainz_id:
                        research_data[album_key(primary.artist, primary.album)] = rd
                        primary.research_available = True
                        _apply_year_override(primary, rd)

                        # Discovery mode: validate against research
                        if is_discovery:
                            valid = await asyncio.to_thread(
                                pipeline.validate_discovery_album,
                                primary, rd, _prompt, request.session_id,
                            )
                            if not valid:
                                logger.info("Primary discovery album failed validation")
                                research_warning = "The primary recommendation could not be fully verified against available sources."

                        await _set_cover_art_from_research(primary, rd, research_client)
                    elif is_discovery:
                        # MusicBrainz couldn't verify this album exists
                        logger.warning("Discovery album not found in MusicBrainz: %s — %s", primary.artist, primary.album)
                        research_warning = "This album could not be verified in MusicBrainz — details may be approximate."
                except Exception as e:
                    logger.warning("Primary research failed: %s", e)
                    research_warning = "Research was unavailable for the primary album — factual details could not be verified and may be approximate."

            # Step 3: Research secondary albums (light research)
            if await _check_disconnect():
                return
            yield f"event: progress\ndata: {json.dumps({'step': 'researching_secondary', 'message': 'Looking up additional picks...'})}\n\n"

            secondaries = [r for r in recommendations if r.rank == "secondary"]
            for sec in secondaries:
                try:
                    rd = await research_client.research_album(sec.artist, sec.album, full=False, year=sec.year)
                    if rd.musicbrainz_id:
                        research_data[album_key(sec.artist, sec.album)] = rd
                        sec.research_available = True
                        _apply_year_override(sec, rd)

                        await _set_cover_art_from_research(sec, rd, research_client)
                except Exception as e:
                    logger.warning("Secondary research failed for %s: %s", sec.album, e)

            # Step 3.5: Extract facts from research (primary album only)
            extracted_facts = {}
            primary_key = album_key(primary.artist, primary.album) if primary else None

            if primary_key and primary_key in research_data:
                yield f"event: progress\ndata: {json.dumps({'step': 'extracting_facts', 'message': 'Analyzing research sources...'})}\n\n"

                try:
                    facts = await asyncio.to_thread(
                        pipeline.extract_facts,
                        artist=primary.artist,
                        album=primary.album,
                        research=research_data[primary_key],
                        session_id=request.session_id,
                    )
                    extracted_facts[primary_key] = facts
                except Exception as e:
                    logger.warning("Fact extraction failed: %s", e)

            # Step 4: Write pitches (with research data, extracted facts, and familiarity)
            if await _check_disconnect():
                return
            yield f"event: progress\ndata: {json.dumps({'step': 'writing', 'message': 'Writing the pitch...'})}\n\n"

            recommendations = await asyncio.to_thread(
                pipeline.write_pitches,
                recommendations=recommendations,
                prompt=_prompt,
                answers=_answers,
                answer_texts=_answer_texts,
                session_id=request.session_id,
                research=research_data if research_data else None,
                familiarity_pref=_familiarity_pref,
                familiarity_data=familiarity_data,
                extracted_facts=extracted_facts if extracted_facts else None,
            )

            # Step 5: Validate primary pitch (only if we have extracted facts)
            if await _check_disconnect():
                return
            if primary and primary_key and primary_key in extracted_facts:
                yield f"event: progress\ndata: {json.dumps({'step': 'validating', 'message': 'Fact-checking the pitch...'})}\n\n"

                try:
                    validation = await asyncio.to_thread(
                        pipeline.validate_pitch,
                        pitch=primary.pitch,
                        facts=extracted_facts[primary_key],
                        session_id=request.session_id,
                    )

                    if not validation.valid:
                        logger.info(
                            "Pitch validation found %d issues, rewriting",
                            len(validation.issues),
                        )
                        yield f"event: progress\ndata: {json.dumps({'step': 'rewriting', 'message': 'Refining the pitch...'})}\n\n"

                        from backend.recommender import format_answers_for_pitch
                        answers_str = format_answers_for_pitch(_answers, _answer_texts)

                        await asyncio.to_thread(
                            pipeline.rewrite_pitch,
                            rec=primary,
                            facts=extracted_facts[primary_key],
                            validation=validation,
                            prompt=_prompt,
                            answers_str=answers_str,
                            session_id=request.session_id,
                        )

                        # Re-validate the rewrite
                        revalidation = await asyncio.to_thread(
                            pipeline.validate_pitch,
                            pitch=primary.pitch,
                            facts=extracted_facts[primary_key],
                            session_id=request.session_id,
                        )

                        if not revalidation.valid:
                            logger.warning(
                                "Pitch still has %d issues after rewrite",
                                len(revalidation.issues),
                            )
                            if not research_warning:
                                research_warning = (
                                    "Some details could not be fully verified "
                                    "against available sources."
                                )
                except Exception as e:
                    logger.warning("Pitch validation failed: %s", e)

            # Set research warning if no research was available at all
            if not research_data:
                research_warning = "Research was unavailable — factual details could not be verified and may be approximate."

            # Final result — read accumulated costs from the pipeline session
            total_tokens, total_cost = pipeline.get_session_costs(request.session_id)
            result = RecommendGenerateResponse(
                recommendations=recommendations,
                token_count=total_tokens,
                estimated_cost=total_cost,
                research_warning=research_warning,
            )

            # Save result to history before emitting the final event
            rec_result_id = None
            try:
                primary_rec = next((r for r in recommendations if r.rank == "primary"), None)
                if primary_rec:
                    rec_title = f"{primary_rec.album} by {primary_rec.artist}"
                    rec_artist = primary_rec.artist
                    rec_art_key = primary_rec.track_rating_keys[0] if primary_rec.track_rating_keys else None
                    rec_subtitle = primary_rec.pitch.hook if primary_rec.pitch and primary_rec.pitch.hook else _prompt
                else:
                    rec_title = "Album Recommendation"
                    rec_artist = None
                    rec_art_key = None
                    rec_subtitle = _prompt
                rec_result_id = await asyncio.to_thread(
                    library_cache.save_result,
                    result_type="album_recommendation",
                    title=rec_title,
                    prompt=_prompt,
                    snapshot=result.model_dump(mode="json"),
                    track_count=len(recommendations),
                    artist=rec_artist,
                    art_rating_key=rec_art_key,
                    subtitle=rec_subtitle,
                )
            except Exception as e:
                logger.warning("Failed to save recommendation result: %s", e)

            # Include result_id in the event payload
            result_payload = result.model_dump(mode="json")
            if rec_result_id:
                result_payload["result_id"] = rec_result_id
            yield f"event: result\ndata: {json.dumps(result_payload)}\n\n"

            # Record shown albums so "Show me another" won't repeat them.
            # Keep only the last 30 (10 rounds) so older picks rotate back in.
            new_keys = [
                album_key(rec.artist, rec.album)
                for rec in recommendations
            ]
            pipeline.update_previously_recommended(request.session_id, new_keys)

            # Log cost summary
            logger.info(
                "recommend.cost_summary | session=%s albums_researched=%d facts_extracted=%d research_warning=%s",
                request.session_id,
                len(research_data),
                len(extracted_facts),
                research_warning is not None,
            )

        except Exception as e:
            logger.exception("Recommendation generation failed")
            # Send user-facing errors (ValueError) as-is; sanitize internal errors
            if isinstance(e, ValueError):
                error_data = json.dumps({"message": str(e)})
            else:
                error_data = json.dumps({"message": "An error occurred during recommendation generation. Please try again."})
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Results Persistence Endpoints
# =============================================================================


_VALID_RESULT_TYPES = {"prompt_playlist", "seed_playlist", "album_recommendation"}


@app.get("/api/results", response_model=ResultListResponse)
async def list_results(
    type: str | None = Query(None, description="Filter by type (comma-separated)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ResultListResponse:
    """List saved results for the history view."""
    if type:
        requested = {t.strip() for t in type.split(",")}
        invalid = requested - _VALID_RESULT_TYPES
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid result type: {', '.join(sorted(invalid))}")
    results, total = await asyncio.to_thread(
        library_cache.list_results, result_type=type, limit=limit, offset=offset
    )
    return ResultListResponse(
        results=[ResultListItem(**r) for r in results],
        total=total,
    )


_RESULT_ID_RE = re.compile(r"^[0-9a-f]{8,16}$")


@app.get("/api/results/{result_id}", response_model=ResultDetail)
async def get_result(result_id: str) -> ResultDetail:
    """Fetch a single saved result with full snapshot."""
    if not _RESULT_ID_RE.match(result_id):
        raise HTTPException(status_code=400, detail="Invalid result ID format")
    result = await asyncio.to_thread(library_cache.get_result, result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    return ResultDetail(**result)


@app.delete("/api/results/{result_id}", status_code=204)
async def delete_result(result_id: str):
    """Delete a saved result."""
    if not _RESULT_ID_RE.match(result_id):
        raise HTTPException(status_code=400, detail="Invalid result ID format")
    deleted = await asyncio.to_thread(library_cache.delete_result, result_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Result not found")
    return Response(status_code=204)


# =============================================================================
# Album Art Proxy
# =============================================================================


@app.get("/api/art/{rating_key}")
async def get_album_art(rating_key: str):
    """Proxy album art from Plex or Jellyfin to avoid exposing credentials to browser."""
    config = get_config()

    if config.media_server == "jellyfin":
        jellyfin_client = get_jellyfin_client()
        if not jellyfin_client or not jellyfin_client.is_connected():
            raise HTTPException(status_code=503, detail="Jellyfin not connected")

        art_url = jellyfin_client.get_art_url(rating_key)
        if art_url:
            try:
                proxy_client = await _get_art_proxy_client()
                response = await proxy_client.get(
                    art_url,
                    headers={"Authorization": f'MediaBrowser Token="{config.jellyfin.token}"'},
                )
                if response.status_code == 200:
                    return Response(
                        content=response.content,
                        media_type=response.headers.get("content-type", "image/jpeg"),
                    )
            except Exception:
                logger.debug("Jellyfin art proxy failed for item_id=%s", rating_key, exc_info=True)

        raise HTTPException(status_code=404, detail="Art not available")

    # Plex path (default)
    if not rating_key.isdigit():
        raise HTTPException(status_code=400, detail="Invalid rating key format")

    plex_client = get_plex_client()
    if not plex_client or not plex_client.is_connected():
        raise HTTPException(status_code=503, detail="Plex not connected")

    # Get raw thumb path from Plex
    thumb_path = await asyncio.to_thread(plex_client.get_thumb_path, rating_key)
    if thumb_path:
        try:
            client = await _get_art_proxy_client()
            thumb_url = f"{config.plex.url}{thumb_path}"
            response = await client.get(
                thumb_url,
                headers={"X-Plex-Token": config.plex.token},
            )
            if response.status_code == 200:
                return Response(
                    content=response.content,
                    media_type=response.headers.get("content-type", "image/jpeg"),
                )
        except Exception:
            logger.debug("Plex art proxy failed for rating_key=%s", rating_key, exc_info=True)

    raise HTTPException(status_code=404, detail="Art not available")


# Allowlist of external art domains (Cover Art Archive CDN).
# Cover Art Archive redirects through archive.org to CDN servers like
# dn710808.ca.archive.org or ia800123.us.archive.org — allow any subdomain.
_EXTERNAL_ART_DOMAINS = {"coverartarchive.org", "archive.org"}


@app.get("/api/external-art")
async def get_external_art(url: str = Query(...)):
    """Proxy external album art (e.g., Cover Art Archive) to avoid direct hotlinking."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    # Only allow HTTPS from known art CDN domains
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Only HTTPS URLs allowed")
    hostname = parsed.hostname or ""
    allowed = any(hostname == d or hostname.endswith(f".{d}") for d in _EXTERNAL_ART_DOMAINS)
    if not allowed:
        raise HTTPException(status_code=400, detail="Domain not allowed")

    try:
        client = await _get_art_proxy_client()
        # Follow redirects manually with domain re-validation on each hop
        current_url = url
        for _ in range(5):
            response = await client.get(current_url, follow_redirects=False)
            if response.status_code == 200:
                return Response(
                    content=response.content,
                    media_type=response.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get("location", "")
                if not redirect_url:
                    break
                redirect_parsed = urlparse(redirect_url)
                redir_host = redirect_parsed.hostname or ""
                redir_allowed = (
                    redirect_parsed.scheme == "https"
                    and any(redir_host == d or redir_host.endswith(f".{d}") for d in _EXTERNAL_ART_DOMAINS)
                )
                if not redir_allowed:
                    break  # Redirect to disallowed domain
                current_url = redirect_url
            else:
                break
    except Exception:
        logger.debug("External art proxy failed for url=%s", url, exc_info=True)

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
    """Serve the main index.html page with cache-busted asset URLs."""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        html = index_path.read_text()
        v = get_version()
        html = html.replace("/static/style.css", f"/static/style.css?v={v}")
        html = html.replace("/static/app.js", f"/static/app.js?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    return {"message": "MediaSage API is running. Frontend not found."}
