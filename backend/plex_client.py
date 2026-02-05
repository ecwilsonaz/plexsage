"""Plex server client for library queries and playlist management."""

import hashlib
import logging
import re
import time
from typing import Any

from plexapi.exceptions import NotFound, Unauthorized
from plexapi.server import PlexServer
from requests.exceptions import ConnectionError, Timeout
from unidecode import unidecode

from backend.models import Track

logger = logging.getLogger(__name__)


class TrackCache:
    """In-memory cache for filtered track results with TTL."""

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 50):
        """Initialize cache with TTL in seconds (default 5 minutes) and max entries."""
        self._cache: dict[str, tuple[list[Track], float]] = {}
        self._ttl = ttl_seconds
        self._max_entries = max_entries

    def _make_key(
        self,
        genres: list[str] | None,
        decades: list[str] | None,
        exclude_live: bool,
        min_rating: int,
    ) -> str:
        """Create deterministic cache key from filter params."""
        key_data = {
            "genres": sorted(genres or []),
            "decades": sorted(decades or []),
            "exclude_live": exclude_live,
            "min_rating": min_rating,
        }
        return hashlib.md5(str(key_data).encode()).hexdigest()

    def _evict_oldest(self) -> None:
        """Evict the oldest entry from the cache."""
        if not self._cache:
            return
        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
        del self._cache[oldest_key]
        logger.info("Evicted oldest cache entry (key=%s)", oldest_key[:8])

    def get(
        self,
        genres: list[str] | None,
        decades: list[str] | None,
        exclude_live: bool,
        min_rating: int,
    ) -> list[Track] | None:
        """Get cached tracks if available and not expired."""
        key = self._make_key(genres, decades, exclude_live, min_rating)
        if key in self._cache:
            tracks, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl:
                logger.info("Cache hit for filters (key=%s)", key[:8])
                return tracks
            else:
                logger.info("Cache expired for filters (key=%s)", key[:8])
                del self._cache[key]
        return None

    def set(
        self,
        genres: list[str] | None,
        decades: list[str] | None,
        exclude_live: bool,
        min_rating: int,
        tracks: list[Track],
    ) -> None:
        """Cache tracks with current timestamp."""
        key = self._make_key(genres, decades, exclude_live, min_rating)

        # Evict oldest if at capacity (and not updating existing key)
        if key not in self._cache and len(self._cache) >= self._max_entries:
            self._evict_oldest()

        self._cache[key] = (tracks, time.time())
        logger.info("Cached %d tracks (key=%s)", len(tracks), key[:8])

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        logger.info("Track cache cleared")


# Global cache instance
_track_cache = TrackCache()


def get_track_cache() -> TrackCache:
    """Get the global track cache instance."""
    return _track_cache


class PlexQueryError(Exception):
    """Raised when a Plex library query fails."""


# Fuzzy matching threshold (0-100)
FUZZ_THRESHOLD = 60

# Patterns for detecting live recordings
DATE_PATTERN = r"\d{4}[-/]\d{2}[-/]\d{2}"
LIVE_KEYWORDS = r"\b(?:live|concert|sbd|bootleg)\b"


def simplify_string(s: str) -> str:
    """Normalize string for fuzzy comparison."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)  # Remove punctuation
    s = unidecode(s)  # Normalize unicode (café → cafe)
    return s


def normalize_artist(name: str) -> list[str]:
    """Return variations of artist name for matching."""
    variations = [name]
    if " and " in name.lower():
        variations.append(name.replace(" and ", " & ").replace(" And ", " & "))
    elif " & " in name:
        variations.append(name.replace(" & ", " and "))
    return variations


def is_live_version(track: Any) -> bool:
    """Check if track appears to be a live recording.

    Args:
        track: Plex track object (raw Plex object or Track model)

    Returns:
        True if track appears to be a live version
    """
    # Use parentTitle first - it's already cached on the track object
    # This avoids a network call per track (track.album() does HTTP request)
    album_title = getattr(track, 'parentTitle', '') or ''

    # Only call track.album() if parentTitle is empty and album() exists
    if not album_title and callable(getattr(track, 'album', None)):
        album = track.album()
        album_title = album.title if album else ""

    track_title = track.title

    for text in [album_title, track_title]:
        if re.search(DATE_PATTERN, text):
            return True
        if re.search(LIVE_KEYWORDS, text, re.IGNORECASE):
            return True

    return False


class PlexClient:
    """Client for interacting with Plex server."""

    def __init__(self, url: str, token: str, music_library: str = "Music"):
        """Initialize Plex client.

        Args:
            url: Plex server URL
            token: Plex authentication token
            music_library: Name of the music library section
        """
        self.url = url
        self.token = token
        self.music_library_name = music_library
        self._server: PlexServer | None = None
        self._library = None
        self._error: str | None = None
        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Plex server."""
        if not self.url or not self.token:
            self._error = "Plex URL and token are required"
            return

        try:
            self._server = PlexServer(self.url, self.token, timeout=30)
            self._library = self._server.library.section(self.music_library_name)
            self._error = None
        except Unauthorized:
            self._error = "Invalid Plex token - unauthorized"
            self._server = None
            self._library = None
        except NotFound:
            self._error = f"Music library '{self.music_library_name}' not found"
            self._library = None
        except ConnectionError:
            self._error = f"Cannot connect to Plex server at {self.url}"
            self._server = None
            self._library = None
        except Timeout:
            self._error = "Connection to Plex server timed out"
            self._server = None
            self._library = None
        except Exception as e:
            self._error = f"Plex connection error: {str(e)}"
            self._server = None
            self._library = None

    def is_connected(self) -> bool:
        """Check if connected to Plex server with valid library."""
        return self._server is not None and self._library is not None

    def get_machine_identifier(self) -> str | None:
        """Get the Plex server's machine identifier."""
        if not self._server:
            return None
        return self._server.machineIdentifier

    def get_error(self) -> str | None:
        """Get the last error message if any."""
        return self._error

    def get_music_libraries(self) -> list[str]:
        """Get list of music library names."""
        if not self._server:
            return []

        try:
            sections = self._server.library.sections()
            return [s.title for s in sections if s.type == "artist"]
        except Exception:
            return []

    def get_library_stats(self) -> dict[str, Any]:
        """Get statistics about the music library.

        Returns:
            Dict with total_tracks, genres, and decades
        """
        if not self._library:
            return {"total_tracks": 0, "genres": [], "decades": []}

        try:
            # Get genres using filter choices API (fast) - works at track level
            # Note: listFilterChoices doesn't provide counts, so we omit them
            genre_choices = self._library.listFilterChoices("genre", libtype="track")
            genres = [
                {"name": g.title, "count": None}
                for g in genre_choices
            ]
            genres = sorted(genres, key=lambda x: x["name"])

            # Get decades using filter choices API at album level
            # (decade filter only exists for albums, not tracks)
            decade_choices = self._library.listFilterChoices("decade", libtype="album")
            decades = []
            for d in decade_choices:
                name = d.title
                if name and not name.endswith('s'):
                    name = f"{name}s"
                decades.append({
                    "name": name,
                    "count": None
                })
            decades = sorted(decades, key=lambda x: x["name"])

            # Get total track count efficiently
            # Use totalSize from search response metadata
            total_tracks = self._library.totalViewSize(libtype="track")

            return {
                "total_tracks": total_tracks,
                "genres": genres,
                "decades": decades,
            }
        except Exception as e:
            return {"total_tracks": 0, "genres": [], "decades": [], "error": str(e)}

    def get_all_tracks(self) -> list[Track]:
        """Get all tracks from the library."""
        if not self._library:
            return []

        try:
            plex_tracks = self._library.search(libtype="track")
            return [self._convert_track(t) for t in plex_tracks]
        except Exception:
            return []

    def get_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
        limit: int = 0,
    ) -> list[Track]:
        """Get tracks matching filter criteria.

        Args:
            genres: List of genre names to include
            decades: List of decades (e.g., ["1990s", "2000s"])
            exclude_live: Whether to exclude live recordings
            min_rating: Minimum user rating (0-10, 0 = no filter)
            limit: Max tracks to return (0 = no limit). When set, uses random
                   server-side sampling for efficiency with large libraries.

        Returns:
            List of matching Track objects
        """
        if not self._library:
            return []

        try:
            filters = self._build_filters(genres, decades, min_rating)

            # When limit is set, use server-side random sampling for efficiency
            # Fetch extra to account for live version filtering
            if limit > 0:
                fetch_count = int(limit * 1.3) if exclude_live else limit
                plex_tracks = self._library.search(
                    libtype="track",
                    sort="random",
                    limit=fetch_count,
                    **filters,
                )
            else:
                plex_tracks = self._library.search(libtype="track", **filters)

            # Post-filter for live versions (can't be done server-side)
            if exclude_live:
                plex_tracks = [t for t in plex_tracks if not is_live_version(t)]

            # Apply limit after live filtering
            if limit > 0:
                plex_tracks = plex_tracks[:limit]

            return [self._convert_track(t) for t in plex_tracks]
        except Exception as e:
            logger.exception("Failed to query Plex library with filters: %s", filters)
            raise PlexQueryError(f"Failed to query Plex library: {e}") from e

    def get_filtered_track_count(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        min_rating: int = 0,
    ) -> int:
        """Get count of tracks matching filter criteria (without live filtering).

        Args:
            genres: List of genre names to include
            decades: List of decades (e.g., ["1990s", "2000s"])
            min_rating: Minimum user rating (0-10, 0 = no filter)

        Returns:
            Count of matching tracks, or -1 if unknown
        """
        if not self._library:
            return 0

        try:
            filters = self._build_filters(genres, decades, min_rating)

            # If no filters, return total track count (fast path)
            if not filters:
                return self._library.totalViewSize(libtype="track")

            # Search and count results
            results = self._library.search(libtype="track", **filters)
            return len(results)
        except Exception:
            return -1

    def count_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
    ) -> int:
        """Count matching tracks without converting to Track objects.

        This is faster than get_tracks_by_filters() when only the count is needed.

        Args:
            genres: List of genre names to include
            decades: List of decades (e.g., ["1990s", "2000s"])
            exclude_live: Whether to exclude live recordings
            min_rating: Minimum user rating (0-10, 0 = no filter)

        Returns:
            Count of matching tracks, or -1 on error
        """
        if not self._library:
            return -1

        try:
            filters = self._build_filters(genres, decades, min_rating)

            # Fast path: no filters and not excluding live
            if not filters and not exclude_live:
                return self._library.totalViewSize(libtype="track")

            # Get raw Plex tracks (no conversion to Track objects)
            plex_tracks = self._library.search(libtype="track", **filters)

            if exclude_live:
                # Count non-live tracks without full conversion
                # is_live_version uses parentTitle which is already cached
                return sum(1 for t in plex_tracks if not is_live_version(t))

            return len(plex_tracks)
        except Exception as e:
            logger.exception("Failed to count tracks with filters: %s", e)
            return -1

    def _build_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        min_rating: int = 0,
    ) -> dict[str, Any]:
        """Build Plex filter kwargs from filter parameters.

        Args:
            genres: List of genre names to include
            decades: List of decades (e.g., ["1990s", "2000s"])
            min_rating: Minimum user rating (0-10, 0 = no filter)

        Returns:
            Dict of filter kwargs for Plex search
        """
        filters = {}

        if genres:
            filters['genre'] = genres

        if decades:
            # Convert decades like "1980s" to decade values "1980"
            decade_values = []
            for d in decades:
                if d.endswith('s'):
                    decade_values.append(d[:-1])
                else:
                    decade_values.append(d)
            if decade_values:
                filters['decade'] = decade_values

        if min_rating > 0:
            filters['userRating>>='] = min_rating

        return filters

    def get_genres(self) -> list[dict[str, Any]]:
        """Get list of genres with track counts."""
        stats = self.get_library_stats()
        return stats.get("genres", [])

    def get_decades(self) -> list[dict[str, Any]]:
        """Get list of decades with track counts."""
        stats = self.get_library_stats()
        return stats.get("decades", [])

    def get_random_tracks(
        self,
        count: int,
        exclude_live: bool = True,
    ) -> list[Track]:
        """Get random tracks from the library without loading all tracks.

        Uses Plex's random sort with limit for efficient sampling.

        Args:
            count: Number of random tracks to fetch
            exclude_live: Whether to exclude live recordings

        Returns:
            List of random Track objects
        """
        if not self._library:
            return []

        try:
            # Fetch more than needed to account for live version filtering
            fetch_count = int(count * 1.3) if exclude_live else count

            plex_tracks = self._library.search(
                libtype="track",
                sort="random",
                limit=fetch_count,
            )

            if exclude_live:
                plex_tracks = [t for t in plex_tracks if not is_live_version(t)]

            tracks = [self._convert_track(t) for t in plex_tracks[:count]]
            return tracks
        except Exception as e:
            logger.exception("Failed to get random tracks: %s", e)
            raise PlexQueryError(f"Failed to get random tracks: {e}") from e

    def search_tracks(self, query: str, limit: int = 20) -> list[Track]:
        """Search for tracks by title or artist.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching Track objects
        """
        if not self._library:
            return []

        try:
            # Search tracks
            results = self._library.searchTracks(title=query, limit=limit)

            # Also search by artist if we have few results
            if len(results) < limit:
                artist_results = self._library.search(libtype="track", limit=limit)
                # Filter by artist name
                artist_matches = [
                    t for t in artist_results
                    if query.lower() in (t.grandparentTitle or "").lower()
                ]
                # Combine and deduplicate
                seen_keys = {t.ratingKey for t in results}
                for t in artist_matches:
                    if t.ratingKey not in seen_keys:
                        results.append(t)
                        seen_keys.add(t.ratingKey)

            return [self._convert_track(t) for t in results[:limit]]
        except Exception:
            return []

    def get_track_by_key(self, rating_key: str) -> Track | None:
        """Get a single track by rating key.

        Args:
            rating_key: Plex rating key

        Returns:
            Track object or None if not found
        """
        if not self._server:
            return None

        try:
            item = self._server.fetchItem(int(rating_key))
            return self._convert_track(item)
        except Exception:
            return None

    def get_thumb_path(self, rating_key: str) -> str | None:
        """Get the raw Plex thumb path for a track.

        Args:
            rating_key: Plex rating key

        Returns:
            Thumb path (e.g., '/library/metadata/123/thumb/456') or None
        """
        if not self._server:
            return None

        try:
            item = self._server.fetchItem(int(rating_key))
            return item.thumb if hasattr(item, "thumb") else None
        except Exception:
            return None

    def create_playlist(self, name: str, rating_keys: list[str]) -> dict[str, Any]:
        """Create a playlist in Plex.

        Args:
            name: Playlist name
            rating_keys: List of track rating keys

        Returns:
            Dict with success status and playlist_id or error
        """
        if not self._server:
            return {"success": False, "error": "Not connected to Plex"}

        try:
            # Fetch track items
            items = []
            skipped_keys = []
            for key in rating_keys:
                try:
                    item = self._server.fetchItem(int(key))
                    items.append(item)
                except Exception as e:
                    logger.warning("Failed to fetch track %s for playlist: %s", key, e)
                    skipped_keys.append(key)

            if skipped_keys:
                logger.info(
                    "Playlist '%s': skipped %d of %d tracks",
                    name,
                    len(skipped_keys),
                    len(rating_keys),
                )

            if not items:
                return {"success": False, "error": "No valid tracks found"}

            # Create playlist
            playlist = self._server.createPlaylist(name, items=items)

            # Build the Plex web app URL for the playlist (uses local server URL)
            playlist_url = None
            machine_id = self.get_machine_identifier()
            if machine_id:
                playlist_url = (
                    f"{self.url}/web/index.html#!/server/{machine_id}"
                    f"/playlist?key=%2Fplaylists%2F{playlist.ratingKey}"
                )

            return {
                "success": True,
                "playlist_id": str(playlist.ratingKey),
                "playlist_url": playlist_url,
                "tracks_added": len(items),
                "tracks_skipped": len(skipped_keys),
            }
        except Exception as e:
            logger.exception("Failed to create playlist '%s'", name)
            return {"success": False, "error": str(e)}

    def _convert_track(self, plex_track: Any) -> Track:
        """Convert a Plex track object to our Track model."""
        # Get genres
        genres = []
        if hasattr(plex_track, "genres"):
            genres = [
                g.tag if hasattr(g, "tag") else str(g)
                for g in plex_track.genres
            ]

        # Get year from album or track
        year = getattr(plex_track, "parentYear", None) or getattr(plex_track, "year", None)

        # Build art URL (will be proxied through our API)
        art_url = f"/api/art/{plex_track.ratingKey}" if plex_track.ratingKey else None

        return Track(
            rating_key=str(plex_track.ratingKey),
            title=plex_track.title,
            artist=plex_track.grandparentTitle or "Unknown Artist",
            album=plex_track.parentTitle or "Unknown Album",
            duration_ms=plex_track.duration or 0,
            year=year,
            genres=genres,
            art_url=art_url,
        )


# Global client instance
_plex_client: PlexClient | None = None


def get_plex_client() -> PlexClient | None:
    """Get the current Plex client instance."""
    return _plex_client


def init_plex_client(url: str, token: str, music_library: str = "Music") -> PlexClient:
    """Initialize or reinitialize the Plex client."""
    global _plex_client
    _plex_client = PlexClient(url, token, music_library)
    return _plex_client
