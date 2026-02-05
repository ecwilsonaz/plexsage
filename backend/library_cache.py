"""Local SQLite cache for Plex library track metadata.

This module provides fast local access to track data by caching Plex library
metadata in a SQLite database. It eliminates the 2+ minute cold start time
for large libraries by syncing once and loading from cache thereafter.
"""

import json
import logging
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Database location
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "library_cache.db"

# Patterns for detecting live recordings (same as plex_client.py)
DATE_PATTERN = r"\d{4}[-/]\d{2}[-/]\d{2}"
LIVE_KEYWORDS = r"\b(?:live|concert|sbd|bootleg)\b"

# Batch size for sync operations (smaller = more frequent progress updates)
SYNC_BATCH_SIZE = 500

# Module-level sync state (in-memory for progress tracking)
_sync_state = {
    "is_syncing": False,
    "phase": None,  # "fetching_albums", "fetching", or "processing"
    "current": 0,
    "total": 0,
    "error": None,
}

# Lock to prevent race conditions when starting sync
_sync_lock = threading.Lock()

# Track if schema has been initialized
_schema_initialized = False


def _is_live_version(title: str, album: str) -> bool:
    """Check if track appears to be a live recording based on title/album."""
    for text in [title, album]:
        if re.search(DATE_PATTERN, text):
            return True
        if re.search(LIVE_KEYWORDS, text, re.IGNORECASE):
            return True
    return False


def get_db_connection() -> sqlite3.Connection:
    """Get a database connection with WAL mode enabled.

    Returns:
        sqlite3.Connection with row_factory set for dict-like access
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row

    # Enable WAL mode for concurrent reads during writes
    conn.execute("PRAGMA journal_mode=WAL")
    # Set busy timeout for lock contention
    conn.execute("PRAGMA busy_timeout=5000")
    # Enable foreign keys (good practice)
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize database schema if not exists.

    Args:
        conn: Database connection
    """
    conn.executescript("""
        -- Tracks table: cached Plex track metadata
        CREATE TABLE IF NOT EXISTS tracks (
            rating_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT NOT NULL,
            duration_ms INTEGER,
            year INTEGER,
            genres TEXT,
            user_rating INTEGER,
            is_live BOOLEAN,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Indexes for common query patterns
        CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
        CREATE INDEX IF NOT EXISTS idx_tracks_year ON tracks(year);
        CREATE INDEX IF NOT EXISTS idx_tracks_is_live ON tracks(is_live);

        -- Sync state: single-row metadata table
        CREATE TABLE IF NOT EXISTS sync_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            plex_server_id TEXT,
            last_sync_at TIMESTAMP,
            track_count INTEGER DEFAULT 0,
            sync_duration_ms INTEGER
        );

        -- Ensure sync_state has exactly one row
        INSERT OR IGNORE INTO sync_state (id) VALUES (1);
    """)
    conn.commit()


def ensure_db_initialized() -> sqlite3.Connection:
    """Ensure database exists and schema is initialized.

    Returns:
        Initialized database connection
    """
    global _schema_initialized
    conn = get_db_connection()

    # Only initialize schema once per process to avoid lock contention
    if not _schema_initialized:
        init_schema(conn)
        _schema_initialized = True

    return conn


def get_sync_state() -> dict[str, Any]:
    """Get current sync state from database and in-memory state.

    Returns:
        Dict with track_count, synced_at, is_syncing, sync_progress, error
    """
    conn = ensure_db_initialized()
    try:
        row = conn.execute(
            "SELECT plex_server_id, last_sync_at, track_count, sync_duration_ms "
            "FROM sync_state WHERE id = 1"
        ).fetchone()

        result = {
            "track_count": row["track_count"] if row else 0,
            "synced_at": row["last_sync_at"] if row else None,
            "plex_server_id": row["plex_server_id"] if row else None,
            "sync_duration_ms": row["sync_duration_ms"] if row else None,
            "is_syncing": _sync_state["is_syncing"],
            "sync_progress": None,
            "error": _sync_state["error"],
        }

        if _sync_state["is_syncing"]:
            result["sync_progress"] = {
                "phase": _sync_state["phase"],
                "current": _sync_state["current"],
                "total": _sync_state["total"],
            }

        return result
    finally:
        conn.close()


def get_cached_tracks() -> list[dict[str, Any]]:
    """Get all tracks from cache.

    Returns:
        List of track dicts with all fields
    """
    conn = ensure_db_initialized()
    try:
        rows = conn.execute(
            "SELECT rating_key, title, artist, album, duration_ms, year, "
            "genres, user_rating, is_live FROM tracks"
        ).fetchall()

        tracks = []
        for row in rows:
            track = dict(row)
            # Parse genres JSON
            if track["genres"]:
                track["genres"] = json.loads(track["genres"])
            else:
                track["genres"] = []
            tracks.append(track)

        return tracks
    finally:
        conn.close()


def get_tracks_by_filters(
    genres: list[str] | None = None,
    decades: list[str] | None = None,
    min_rating: int = 0,
    exclude_live: bool = True,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Get tracks from cache matching filter criteria.

    Args:
        genres: List of genre names to include (OR matching)
        decades: List of decades like "1990s" (OR matching)
        min_rating: Minimum user rating (0-10, 0 = no filter)
        exclude_live: Whether to exclude live recordings
        limit: Max tracks to return (0 = no limit)

    Returns:
        List of matching track dicts
    """
    conn = ensure_db_initialized()
    try:
        conditions = []
        params: list[Any] = []

        if exclude_live:
            conditions.append("is_live = 0")

        if min_rating > 0:
            conditions.append("user_rating >= ?")
            params.append(min_rating)

        if decades:
            decade_conditions = []
            for decade in decades:
                # Convert "1990s" to year range
                if decade.endswith("s"):
                    start_year = int(decade[:-1])
                else:
                    start_year = int(decade)
                end_year = start_year + 9
                decade_conditions.append("(year >= ? AND year <= ?)")
                params.extend([start_year, end_year])
            if decade_conditions:
                conditions.append(f"({' OR '.join(decade_conditions)})")

        # Build query
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM tracks WHERE {where_clause}"

        # Only apply SQL LIMIT when no genre filter (genre filtering happens in Python)
        # If genres are specified, we need all matching tracks first, then filter, then sample
        if limit > 0 and not genres:
            query += " ORDER BY RANDOM() LIMIT ?"
            params.append(limit)

        rows = conn.execute(query, params).fetchall()
        tracks = []

        for row in rows:
            track = dict(row)
            # Parse genres JSON
            if track["genres"]:
                track["genres"] = json.loads(track["genres"])
            else:
                track["genres"] = []

            # Genre filtering in Python (JSON field doesn't support SQL IN)
            if genres:
                track_genres = [g.lower() for g in track["genres"]]
                if not any(g.lower() in track_genres for g in genres):
                    continue

            tracks.append(track)

        # Apply limit after genre filtering with random sampling
        if limit > 0 and genres and len(tracks) > limit:
            tracks = random.sample(tracks, limit)

        return tracks
    finally:
        conn.close()


def clear_cache() -> None:
    """Clear all cached tracks and reset sync state."""
    conn = ensure_db_initialized()
    try:
        conn.execute("DELETE FROM tracks")
        conn.execute(
            "UPDATE sync_state SET last_sync_at = NULL, track_count = 0, "
            "sync_duration_ms = NULL WHERE id = 1"
        )
        conn.commit()
        logger.info("Cache cleared")
    finally:
        conn.close()


def is_cache_stale(max_age_hours: int = 24) -> bool:
    """Check if cache is older than max_age_hours.

    Args:
        max_age_hours: Maximum cache age in hours

    Returns:
        True if cache is stale or empty
    """
    state = get_sync_state()
    if not state["synced_at"]:
        return True

    try:
        # Parse ISO timestamp
        synced_at = datetime.fromisoformat(state["synced_at"].replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - synced_at).total_seconds() / 3600
        return age_hours > max_age_hours
    except (ValueError, TypeError):
        return True


def check_server_changed(current_server_id: str) -> bool:
    """Check if Plex server has changed since last sync.

    Args:
        current_server_id: Current Plex server's machineIdentifier

    Returns:
        True if server changed (cache should be cleared)
    """
    state = get_sync_state()
    cached_server_id = state.get("plex_server_id")

    if not cached_server_id:
        return False  # First sync, no change

    return cached_server_id != current_server_id


def sync_library(
    plex_client: Any,
    on_progress: callable | None = None,
) -> dict[str, Any]:
    """Sync tracks from Plex to local cache.

    This is a blocking synchronous operation. For async usage, wrap in
    asyncio.to_thread() or run in a thread pool.

    Args:
        plex_client: PlexClient instance with active connection
        on_progress: Optional callback(current, total) for progress updates

    Returns:
        Dict with success, track_count, duration_ms, error
    """
    global _sync_state

    # Use lock to prevent race condition between check and set
    with _sync_lock:
        if _sync_state["is_syncing"]:
            return {"success": False, "error": "Sync already in progress"}

        _sync_state = {
            "is_syncing": True,
            "phase": "fetching_albums",
            "current": 0,
            "total": 0,
            "error": None,
        }

    start_time = time.time()
    conn = None

    try:
        # Get server ID for cache validation
        server_id = plex_client.get_machine_identifier()
        if not server_id:
            raise ValueError("Could not get Plex server identifier")

        # Check if server changed - clear cache if so
        if check_server_changed(server_id):
            logger.info("Plex server changed, clearing cache")
            clear_cache()

        conn = ensure_db_initialized()

        # Phase 1: Fetch albums for genre/year mapping
        logger.info("Fetching album metadata from Plex...")
        album_metadata = plex_client.get_all_albums_metadata()
        logger.info("Got metadata for %d albums", len(album_metadata))

        # Phase 2: Fetch all tracks from Plex
        _sync_state["phase"] = "fetching"
        logger.info("Fetching all tracks from Plex (this may take 30-60s)...")
        all_tracks = plex_client.get_all_raw_tracks()
        total = len(all_tracks)
        logger.info("Got %d tracks from Plex", total)

        _sync_state["total"] = total
        _sync_state["phase"] = "processing"

        # Phase 3: Process tracks in batches with album metadata lookup
        synced_count = 0
        batch_data = []

        for i, track in enumerate(all_tracks):
            # Extract track data
            title = track.title
            album = getattr(track, "parentTitle", "") or ""
            artist = getattr(track, "grandparentTitle", "") or "Unknown Artist"

            # Look up genres and year from album metadata using parentRatingKey
            parent_key = str(getattr(track, "parentRatingKey", ""))
            album_data = album_metadata.get(parent_key, {})
            genres = album_data.get("genres", [])
            year = album_data.get("year")

            batch_data.append((
                str(track.ratingKey),
                title,
                artist,
                album,
                track.duration or 0,
                year,
                json.dumps(genres),  # Store genres as JSON array
                getattr(track, "userRating", None),
                _is_live_version(title, album),
            ))

            # Insert and update progress every SYNC_BATCH_SIZE tracks
            if len(batch_data) >= SYNC_BATCH_SIZE:
                conn.executemany(
                    "INSERT OR REPLACE INTO tracks "
                    "(rating_key, title, artist, album, duration_ms, year, genres, "
                    "user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch_data,
                )
                synced_count += len(batch_data)
                batch_data = []

                # Update progress
                _sync_state["current"] = synced_count
                if on_progress:
                    on_progress(synced_count, total)

                logger.info("Synced %d/%d tracks", synced_count, total)

                # Commit every batch to allow concurrent reads (WAL mode)
                conn.commit()

        # Insert remaining tracks
        if batch_data:
            conn.executemany(
                "INSERT OR REPLACE INTO tracks "
                "(rating_key, title, artist, album, duration_ms, year, genres, "
                "user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch_data,
            )
            synced_count += len(batch_data)
            _sync_state["current"] = synced_count

        # Final commit
        conn.commit()

        # Update sync state
        duration_ms = int((time.time() - start_time) * 1000)
        synced_at = datetime.now(timezone.utc).isoformat()

        conn.execute(
            "UPDATE sync_state SET plex_server_id = ?, last_sync_at = ?, "
            "track_count = ?, sync_duration_ms = ? WHERE id = 1",
            (server_id, synced_at, synced_count, duration_ms),
        )
        conn.commit()

        logger.info("Sync complete: %d tracks in %dms", synced_count, duration_ms)

        return {
            "success": True,
            "track_count": synced_count,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        logger.exception("Sync failed: %s", e)
        _sync_state["error"] = str(e)
        return {"success": False, "error": str(e)}

    finally:
        _sync_state["is_syncing"] = False
        _sync_state["phase"] = None
        _sync_state["current"] = 0
        _sync_state["total"] = 0
        if conn:
            conn.close()


def get_sync_progress() -> dict[str, Any]:
    """Get current sync progress (for polling).

    Returns:
        Dict with is_syncing, phase, current, total, error
    """
    return {
        "is_syncing": _sync_state["is_syncing"],
        "phase": _sync_state["phase"],
        "current": _sync_state["current"],
        "total": _sync_state["total"],
        "error": _sync_state["error"],
    }


def count_tracks_by_filters(
    genres: list[str] | None = None,
    decades: list[str] | None = None,
    min_rating: int = 0,
    exclude_live: bool = True,
) -> int:
    """Count tracks matching filter criteria without fetching full data.

    Args:
        genres: List of genre names to include (OR matching)
        decades: List of decades like "1990s" (OR matching)
        min_rating: Minimum user rating (0-10, 0 = no filter)
        exclude_live: Whether to exclude live recordings

    Returns:
        Count of matching tracks, or -1 if cache is empty
    """
    state = get_sync_state()
    if state["track_count"] == 0:
        return -1  # Cache empty, signal to use Plex

    conn = ensure_db_initialized()
    try:
        conditions = []
        params: list[Any] = []

        if exclude_live:
            conditions.append("is_live = 0")

        if min_rating > 0:
            conditions.append("user_rating >= ?")
            params.append(min_rating)

        if decades:
            decade_conditions = []
            for decade in decades:
                if decade.endswith("s"):
                    start_year = int(decade[:-1])
                else:
                    start_year = int(decade)
                end_year = start_year + 9
                decade_conditions.append("(year >= ? AND year <= ?)")
                params.extend([start_year, end_year])
            if decade_conditions:
                conditions.append(f"({' OR '.join(decade_conditions)})")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # No genre filter - use simple count query
        if not genres:
            query = f"SELECT COUNT(*) FROM tracks WHERE {where_clause}"
            count = conn.execute(query, params).fetchone()[0]
            return count

        # Genre filter - need to check JSON field, so fetch and filter in Python
        query = f"SELECT genres FROM tracks WHERE {where_clause}"
        rows = conn.execute(query, params).fetchall()

        count = 0
        genres_lower = [g.lower() for g in genres]
        for row in rows:
            if row["genres"]:
                track_genres = json.loads(row["genres"])
                track_genres_lower = [g.lower() for g in track_genres]
                if any(g in track_genres_lower for g in genres_lower):
                    count += 1

        return count
    finally:
        conn.close()


def has_cached_tracks() -> bool:
    """Check if cache has any tracks.

    Returns:
        True if cache is populated
    """
    state = get_sync_state()
    return state["track_count"] > 0
