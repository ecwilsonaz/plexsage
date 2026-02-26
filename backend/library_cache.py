"""Local SQLite cache for Plex library track metadata.

This module provides fast local access to track data by caching Plex library
metadata in a SQLite database. It eliminates the 2+ minute cold start time
for large libraries by syncing once and loading from cache thereafter.
"""

import json
import logging
import random
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
_schema_lock = threading.Lock()


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


def init_schema(conn: sqlite3.Connection) -> bool:
    """Initialize database schema if not exists.

    Args:
        conn: Database connection

    Returns:
        True if a schema migration was applied (existing tracks need re-sync),
        False if schema was already up-to-date or freshly created.
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
            parent_rating_key TEXT,
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

        -- Results table: persistent storage for generated playlists and recommendations
        CREATE TABLE IF NOT EXISTS results (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            snapshot JSON NOT NULL,
            track_count INTEGER NOT NULL,
            artist TEXT,
            art_rating_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_results_type_created ON results(type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_results_created_at ON results(created_at DESC);
    """)

    # Migration: add parent_rating_key column if missing (for pre-existing databases)
    migrated = False
    try:
        conn.execute("ALTER TABLE tracks ADD COLUMN parent_rating_key TEXT")
        migrated = True
        logger.info("Migration applied: added parent_rating_key column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add view_count and last_viewed_at columns for familiarity tracking
    try:
        conn.execute("ALTER TABLE tracks ADD COLUMN view_count INTEGER DEFAULT 0")
        migrated = True
        logger.info("Migration applied: added view_count column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        conn.execute("ALTER TABLE tracks ADD COLUMN last_viewed_at TEXT")
        migrated = True
        logger.info("Migration applied: added last_viewed_at column")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add subtitle column to results table
    try:
        conn.execute("ALTER TABLE results ADD COLUMN subtitle TEXT")
        logger.info("Migration applied: added subtitle column to results")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Index on parent_rating_key (must come after migration adds the column)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_parent_key ON tracks(parent_rating_key)")

    conn.commit()
    return migrated


# Whether a migration was applied on startup (signals need for re-sync)
_migration_applied = False


def ensure_db_initialized() -> sqlite3.Connection:
    """Ensure database exists and schema is initialized.

    Returns:
        Initialized database connection
    """
    global _schema_initialized, _migration_applied
    conn = get_db_connection()

    # Only initialize schema once per process; lock prevents races on startup
    if not _schema_initialized:
        with _schema_lock:
            if not _schema_initialized:
                _migration_applied = init_schema(conn)
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

        # Snapshot sync state under lock for consistent reads
        with _sync_lock:
            ss = dict(_sync_state)

        result = {
            "track_count": row["track_count"] if row else 0,
            "synced_at": row["last_sync_at"] if row else None,
            "plex_server_id": row["plex_server_id"] if row else None,
            "sync_duration_ms": row["sync_duration_ms"] if row else None,
            "is_syncing": ss["is_syncing"],
            "sync_progress": None,
            "error": ss["error"],
        }

        if ss["is_syncing"]:
            result["sync_progress"] = {
                "phase": ss["phase"],
                "current": ss["current"],
                "total": ss["total"],
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
                try:
                    start_year = int(decade.rstrip("s"))
                except ValueError:
                    continue
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
    on_progress: Callable[[int, int], None] | None = None,
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

        # Clear existing tracks and reset sync state to avoid stale "cache available"
        # signal if sync fails partway through
        conn.execute("DELETE FROM tracks")
        conn.execute("UPDATE sync_state SET track_count = 0 WHERE id = 1")
        conn.commit()

        # Detect client type: Plex (has get_all_raw_tracks) vs generic (Track models)
        use_raw = hasattr(plex_client, "get_all_raw_tracks")

        if use_raw:
            # Phase 1 (Plex): Fetch albums for genre/year mapping
            logger.info("Fetching album metadata from media server...")
            album_metadata = plex_client.get_all_albums_metadata()
            logger.info("Got metadata for %d albums", len(album_metadata))

            # Phase 2 (Plex): Fetch all raw tracks
            with _sync_lock:
                _sync_state["phase"] = "fetching"
            logger.info("Fetching all tracks from media server (this may take 30-60s)...")
            all_tracks = plex_client.get_all_raw_tracks()
        else:
            # Phase 1+2 (Jellyfin/generic): Fetch Track models directly
            with _sync_lock:
                _sync_state["phase"] = "fetching"
            logger.info("Fetching all tracks from media server (this may take 30-60s)...")
            all_tracks = plex_client.get_all_tracks()
            album_metadata = {}  # Not needed; Track models already have genres/year

        total = len(all_tracks)
        logger.info("Got %d tracks from media server", total)

        with _sync_lock:
            _sync_state["total"] = total
            _sync_state["phase"] = "processing"

        # Phase 3: Process tracks in batches
        synced_count = 0
        batch_data = []

        for i, track in enumerate(all_tracks):
            if use_raw:
                # Raw Plex track objects
                title = track.title
                album = getattr(track, "parentTitle", "") or ""
                artist = getattr(track, "grandparentTitle", "") or "Unknown Artist"
                parent_key = str(getattr(track, "parentRatingKey", ""))
                album_data = album_metadata.get(parent_key, {})
                genres = album_data.get("genres", [])
                year = album_data.get("year")
                view_count = getattr(track, "viewCount", 0) or 0
                last_viewed_at_raw = getattr(track, "lastViewedAt", None)
                last_viewed_at = last_viewed_at_raw.isoformat() if last_viewed_at_raw else None
                rating_key = str(track.ratingKey)
                duration_ms = track.duration or 0
                user_rating = getattr(track, "userRating", None)
            else:
                # Track model objects (Jellyfin)
                title = track.title
                album = track.album
                artist = track.artist or "Unknown Artist"
                parent_key = ""
                genres = track.genres
                year = track.year
                view_count = 0
                last_viewed_at = None
                rating_key = track.rating_key
                duration_ms = track.duration_ms
                user_rating = track.user_rating

            batch_data.append((
                rating_key,
                title,
                artist,
                album,
                duration_ms,
                year,
                json.dumps(genres),  # Store genres as JSON array
                user_rating,
                _is_live_version(title, album),
                parent_key,
                view_count,
                last_viewed_at,
            ))

            # Insert and update progress every SYNC_BATCH_SIZE tracks
            if len(batch_data) >= SYNC_BATCH_SIZE:
                conn.executemany(
                    "INSERT OR REPLACE INTO tracks "
                    "(rating_key, title, artist, album, duration_ms, year, genres, "
                    "user_rating, is_live, parent_rating_key, view_count, last_viewed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch_data,
                )
                synced_count += len(batch_data)
                batch_data = []

                # Update progress (single field, atomic under CPython GIL)
                with _sync_lock:
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
                "user_rating, is_live, parent_rating_key, view_count, last_viewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch_data,
            )
            synced_count += len(batch_data)
            with _sync_lock:
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

        # Clear migration flag — new columns are now populated
        global _migration_applied
        _migration_applied = False

        return {
            "success": True,
            "track_count": synced_count,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        logger.exception("Sync failed: %s", e)
        with _sync_lock:
            _sync_state["error"] = str(e)
        return {"success": False, "error": str(e)}

    finally:
        with _sync_lock:
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
    with _sync_lock:
        return dict(_sync_state)


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
                try:
                    start_year = int(decade.rstrip("s"))
                except ValueError:
                    continue
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


def needs_resync() -> bool:
    """Check if a schema migration was applied that requires a re-sync.

    Returns:
        True if a migration was applied and sync hasn't completed yet.
        Safe for fresh DBs: _migration_applied is False when CREATE TABLE
        already includes all columns (ALTER TABLE no-ops).
    """
    return _migration_applied


def get_album_candidates(
    genres: list[str] | None = None,
    decades: list[str] | None = None,
    exclude_live: bool = True,
) -> list[dict[str, Any]]:
    """Get album candidates aggregated from cached tracks.

    Groups tracks by parent_rating_key to produce album-level data.
    Supports genre/decade filtering.

    Args:
        genres: Optional genre filter (OR matching)
        decades: Optional decade filter (OR matching, e.g. "1990s")
        exclude_live: Exclude live recordings (default True)

    Returns:
        List of album dicts with parent_rating_key, album, album_artist,
        year, genres, decade, track_count, track_rating_keys.
    """
    conn = ensure_db_initialized()
    try:
        conditions = ["parent_rating_key IS NOT NULL", "parent_rating_key != ''"]
        if exclude_live:
            conditions.append("is_live = 0")
        params: list[Any] = []

        if decades:
            decade_conditions = []
            for decade in decades:
                try:
                    start_year = int(decade.rstrip("s"))
                except ValueError:
                    continue
                end_year = start_year + 9
                decade_conditions.append("(year >= ? AND year <= ?)")
                params.extend([start_year, end_year])
            if decade_conditions:
                conditions.append(f"({' OR '.join(decade_conditions)})")

        where_clause = " AND ".join(conditions)
        query = (
            f"SELECT rating_key, title, artist, album, year, genres, parent_rating_key "
            f"FROM tracks WHERE {where_clause} "
            f"ORDER BY parent_rating_key, rating_key"
        )

        rows = conn.execute(query, params).fetchall()

        # Aggregate tracks into albums
        albums: dict[str, dict[str, Any]] = {}
        for row in rows:
            prk = row["parent_rating_key"]
            track_genres = json.loads(row["genres"]) if row["genres"] else []

            if prk not in albums:
                # Derive decade from year
                year = row["year"]
                decade = ""
                if year:
                    decade_start = (year // 10) * 10
                    decade = f"{decade_start}s"

                albums[prk] = {
                    "parent_rating_key": prk,
                    "album": row["album"],
                    # artist column stores grandparentTitle (album artist), not track artist
                    "album_artist": row["artist"],
                    "year": year,
                    "genres": [],
                    "decade": decade,
                    "track_count": 0,
                    "track_rating_keys": [],
                    "_genre_set": set(),
                }

            album = albums[prk]
            album["track_count"] += 1
            album["track_rating_keys"].append(row["rating_key"])
            for g in track_genres:
                if g not in album["_genre_set"]:
                    album["_genre_set"].add(g)
                    album["genres"].append(g)

        # Apply genre filter in Python (genres stored as JSON)
        result = []
        genres_lower = [g.lower() for g in genres] if genres else None
        for album in albums.values():
            # Remove internal tracking set
            del album["_genre_set"]

            if genres_lower:
                album_genres_lower = [g.lower() for g in album["genres"]]
                if not any(g in album_genres_lower for g in genres_lower):
                    continue

            result.append(album)

        return result
    finally:
        conn.close()


def get_cached_genre_decade_stats() -> dict[str, list[dict[str, Any]]]:
    """Get genre and decade stats from the local cache.

    Returns genre/decade lists derived from cached tracks, avoiding a
    round-trip to the Plex server.

    Returns:
        Dict with 'genres' and 'decades' lists, each containing
        {'name': str, 'count': int} dicts sorted by name.
    """
    conn = ensure_db_initialized()
    try:
        rows = conn.execute("SELECT genres, year FROM tracks").fetchall()

        genre_counts: dict[str, int] = {}
        decade_counts: dict[str, int] = {}

        for row in rows:
            # Tally genres
            if row["genres"]:
                for g in json.loads(row["genres"]):
                    genre_counts[g] = genre_counts.get(g, 0) + 1

            # Tally decades
            year = row["year"]
            if year:
                decade_start = (year // 10) * 10
                decade_name = f"{decade_start}s"
                decade_counts[decade_name] = decade_counts.get(decade_name, 0) + 1

        genres = sorted(
            [{"name": name, "count": count} for name, count in genre_counts.items()],
            key=lambda x: x["name"],
        )
        decades = sorted(
            [{"name": name, "count": count} for name, count in decade_counts.items()],
            key=lambda x: x["name"],
        )

        return {"genres": genres, "decades": decades}
    finally:
        conn.close()


def get_album_familiarity(
    parent_rating_keys: list[str] | None = None,
) -> dict[str, dict]:
    """Get familiarity data for albums aggregated from cached track play history.

    Classifies each album as:
    - "unplayed": 0 total plays across all tracks
    - "well-loved": avg plays per track >= 3
    - "light": some plays but avg < 3

    Args:
        parent_rating_keys: Optional list of album keys to query.
            If None, returns all albums.

    Returns:
        Dict mapping parent_rating_key -> {"level": str, "last_viewed_at": str|None}
    """
    conn = ensure_db_initialized()
    try:
        query = (
            "SELECT parent_rating_key, "
            "SUM(view_count) AS total_plays, "
            "AVG(view_count) AS avg_plays, "
            "MAX(last_viewed_at) AS last_viewed "
            "FROM tracks "
            "WHERE parent_rating_key IS NOT NULL AND parent_rating_key != '' "
        )
        params: list[str] = []

        if parent_rating_keys is not None:
            placeholders = ",".join("?" for _ in parent_rating_keys)
            query += f"AND parent_rating_key IN ({placeholders}) "
            params.extend(parent_rating_keys)

        query += "GROUP BY parent_rating_key"

        rows = conn.execute(query, params).fetchall()

        result: dict[str, dict] = {}
        for row in rows:
            total_plays = row["total_plays"] or 0
            avg_plays = row["avg_plays"] or 0

            if total_plays == 0:
                level = "unplayed"
            elif avg_plays >= 3:
                level = "well-loved"
            else:
                level = "light"

            result[row["parent_rating_key"]] = {
                "level": level,
                "last_viewed_at": row["last_viewed"],
            }

        return result
    finally:
        conn.close()


# =============================================================================
# Results Persistence
# =============================================================================


def save_result(
    result_type: str,
    title: str,
    prompt: str,
    snapshot: dict,
    track_count: int,
    artist: str | None = None,
    art_rating_key: str | None = None,
    subtitle: str | None = None,
) -> str:
    """Save a generated result and return its unique ID.

    Args:
        result_type: "prompt_playlist", "seed_playlist", or "album_recommendation"
        title: Display title for the result
        prompt: Original user prompt
        snapshot: Full serialized response (GenerateResponse or RecommendGenerateResponse)
        track_count: Number of tracks in the result
        artist: Primary artist (for album recs)
        art_rating_key: Rating key for thumbnail art
        subtitle: Pre-computed subtitle for history feed cards

    Returns:
        16-char hex ID for the saved result
    """
    conn = ensure_db_initialized()
    try:
        # Generate collision-resistant ID with INSERT OR IGNORE to handle
        # concurrent inserts that race past the existence check.
        for _ in range(10):
            result_id = secrets.token_hex(8)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO results (id, type, title, prompt, snapshot, track_count, artist, art_rating_key, subtitle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result_id, result_type, title, prompt, json.dumps(snapshot), track_count, artist, art_rating_key, subtitle),
            )
            if cursor.rowcount > 0:
                break
        else:
            raise RuntimeError("Failed to generate unique result ID after 10 attempts")
        conn.commit()
        logger.info("Saved result %s (type=%s, tracks=%d)", result_id, result_type, track_count)
        return result_id
    finally:
        conn.close()


def get_result(result_id: str) -> dict[str, Any] | None:
    """Fetch a single result by ID, including its snapshot.

    Returns:
        Dict with all columns (snapshot parsed from JSON), or None if not found.
    """
    conn = ensure_db_initialized()
    try:
        row = conn.execute(
            "SELECT id, type, title, prompt, snapshot, track_count, artist, art_rating_key, subtitle, created_at FROM results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "prompt": row["prompt"],
            "snapshot": json.loads(row["snapshot"]),
            "track_count": row["track_count"],
            "artist": row["artist"],
            "art_rating_key": row["art_rating_key"],
            "subtitle": row["subtitle"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def list_results(
    result_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List results ordered by created_at DESC, without snapshots.

    Args:
        result_type: Optional type filter (can be comma-separated for multiple)
        limit: Max results to return
        offset: Pagination offset

    Returns:
        Tuple of (list of result dicts without snapshot, total count)
    """
    conn = ensure_db_initialized()
    try:
        where_clause = ""
        params: list[Any] = []

        if result_type:
            types = [t.strip() for t in result_type.split(",") if t.strip()]
            placeholders = ",".join("?" for _ in types)
            where_clause = f"WHERE type IN ({placeholders})"
            params = types

        # Get total count
        total = conn.execute(
            f"SELECT COUNT(*) FROM results {where_clause}", params
        ).fetchone()[0]

        # Get page
        rows = conn.execute(
            f"""SELECT id, type, title, prompt, track_count, artist, art_rating_key, subtitle, created_at
                FROM results {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        results = [
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "prompt": row["prompt"],
                "track_count": row["track_count"],
                "artist": row["artist"],
                "art_rating_key": row["art_rating_key"],
                "subtitle": row["subtitle"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return results, total
    finally:
        conn.close()


def delete_result(result_id: str) -> bool:
    """Delete a result by ID.

    Returns:
        True if a row was deleted, False if not found.
    """
    conn = ensure_db_initialized()
    try:
        cursor = conn.execute("DELETE FROM results WHERE id = ?", (result_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Deleted result %s", result_id)
        return deleted
    finally:
        conn.close()
