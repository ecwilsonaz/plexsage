"""Plex server client for library queries and playlist management."""

import hashlib
import logging
import re
import threading
import time
from typing import Any

from plexapi.exceptions import NotFound, Unauthorized
from plexapi.playqueue import PlayQueue
from plexapi.server import PlexServer
from requests.exceptions import ConnectionError, Timeout
from unidecode import unidecode

from backend.media_client import BaseMediaClient
from backend.models import PlexClientInfo, PlexPlaylistInfo, Track

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


class PlexClient(BaseMediaClient):
    """Client for interacting with Plex server."""

    # Cooldown between reconnection attempts (seconds)
    RECONNECT_COOLDOWN = 30

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
        self._last_reconnect_attempt: float = time.time()
        self._reconnect_lock = threading.Lock()
        self._scratch_lock = threading.Lock()
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
        """Check if connected to Plex server with valid library.

        If not connected, attempts to reconnect (with cooldown to avoid hammering).
        Thread-safe to prevent multiple simultaneous reconnection attempts.
        """
        if self._server is not None and self._library is not None:
            return True

        # Not connected - try to reconnect if cooldown has passed
        now = time.time()
        with self._reconnect_lock:
            # Re-check connection inside lock (may have connected while waiting)
            if self._server is not None and self._library is not None:
                return True

            if now - self._last_reconnect_attempt >= self.RECONNECT_COOLDOWN:
                self._last_reconnect_attempt = now
                logger.info("Attempting to reconnect to Plex server...")
                self._connect()

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

    def get_library_total_tracks(self) -> int:
        """Get total track count in the music library.

        Returns:
            Total number of tracks, or 0 if not connected
        """
        if not self._library:
            return 0

        try:
            return self._library.totalViewSize(libtype="track")
        except Exception as e:
            logger.exception("Failed to get library track count: %s", e)
            return 0

    def get_all_raw_tracks(self) -> list[Any]:
        """Get all raw track objects from the library.

        This fetches all tracks in a single API call. For large libraries
        (30k+ tracks), this may take 30-60 seconds.

        Returns:
            List of raw Plex track objects
        """
        if not self._library:
            return []

        try:
            logger.info("Fetching all tracks from Plex (this may take a while)...")
            return self._library.search(libtype="track")
        except Exception as e:
            logger.exception("Failed to get all tracks: %s", e)
            return []

    def get_all_albums_metadata(self) -> dict[str, dict[str, Any]]:
        """Fetch all albums and return mapping of rating_key -> metadata.

        Returns:
            Dict mapping album rating_key (as string) to dict with 'genres' and 'year'
        """
        if not self._library:
            return {}

        try:
            logger.info("Fetching all albums for metadata mapping...")
            albums = self._library.search(libtype="album")
            album_metadata = {
                str(album.ratingKey): {
                    "genres": [g.tag for g in album.genres],
                    "year": getattr(album, "year", None),
                }
                for album in albums
            }
            logger.info("Got metadata for %d albums", len(album_metadata))
            return album_metadata
        except Exception as e:
            logger.exception("Failed to get album metadata: %s", e)
            return {}

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

        Walks the Plex hierarchy: track thumb → album thumb → artist thumb.
        Compilation/soundtrack albums often lack track and album art, so
        falling back to the artist thumb avoids blank covers.

        Args:
            rating_key: Plex rating key

        Returns:
            Thumb path (e.g., '/library/metadata/123/thumb/456') or None
        """
        if not self._server:
            return None

        try:
            item = self._server.fetchItem(int(rating_key))
            return (
                getattr(item, "thumb", None)
                or getattr(item, "parentThumb", None)
                or getattr(item, "grandparentThumb", None)
            )
        except Exception:
            return None

    def get_art_url(self, item_id: str) -> str | None:
        """Get the proxied art URL for a track/album item.

        Returns the Plex thumb path (relative) which main.py will proxy with auth.
        We return the thumb path here; main.py appends the base URL and token.
        """
        return self.get_thumb_path(item_id)

    def create_playlist(
        self, name: str, rating_keys: list[str], description: str = ""
    ) -> dict[str, Any]:
        """Create a playlist in Plex.

        Args:
            name: Playlist name
            rating_keys: List of track rating keys
            description: Playlist description/summary (optional)

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

            # Set description/summary if provided
            if description:
                try:
                    playlist.edit(summary=description)
                    logger.info("Set playlist description: %d chars", len(description))
                except Exception as e:
                    logger.warning("Failed to set playlist description: %s", e)

            playlist_url = self._build_playlist_url(playlist.ratingKey)

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

    _MOBILE_KEYWORDS = {"ios", "android", "iphone", "ipad", "ipod", "tvos"}

    @classmethod
    def _is_mobile_client(cls, product: str, platform: str) -> bool:
        """Check if a client is a mobile/TV device that needs an active session."""
        tokens = set(re.split(r"[\s,/]+", f"{product} {platform}".lower()))
        return bool(tokens & cls._MOBILE_KEYWORDS)

    def get_clients(self) -> list[PlexClientInfo]:
        """Get online Plex clients capable of playback.

        Discovers clients via two methods:
        1. server.clients() — local GDM-discovered clients
        2. myPlexAccount().resources() — cloud-connected players (Plexamp, etc.)

        Returns:
            List of PlexClientInfo for clients with playback capability.
            Unresponsive clients are silently excluded.
        """
        if not self._server:
            return []

        result = []
        seen_ids: set[str] = set()

        # Method 1: Local GDM discovery
        try:
            local_clients = self._server.clients()
        except Exception as e:
            logger.warning("Failed to get local Plex clients: %s", e)
            local_clients = []

        for client in local_clients:
            capabilities = getattr(client, "protocolCapabilities", None) or []
            if isinstance(capabilities, str):
                capabilities = [c.strip() for c in capabilities.split(",")]
            if "playback" not in capabilities:
                continue

            try:
                is_playing = client.isPlayingMedia(includePaused=True)
            except Exception:
                logger.warning("Client '%s' unresponsive, skipping", getattr(client, "title", "unknown"))
                continue

            mid = client.machineIdentifier
            seen_ids.add(mid)
            result.append(PlexClientInfo(
                client_id=mid,
                name=client.title,
                product=client.product,
                platform=client.platform,
                is_playing=is_playing,
                is_mobile=self._is_mobile_client(client.product, client.platform),
            ))

        local_count = len(result)

        # Method 2: Cloud-connected players via account resources
        try:
            account = self._server.myPlexAccount()
            resources = account.resources()

            # Pre-fetch sessions once for all cloud resources
            playing_ids: set[str] = set()
            try:
                for session in self._server.sessions():
                    if session.player:
                        playing_ids.add(session.player.machineIdentifier)
            except Exception:
                pass

            for resource in resources:
                provides = getattr(resource, "provides", "") or ""
                if "player" not in provides:
                    continue
                # Skip if already found via local discovery
                client_id = resource.clientIdentifier
                if client_id in seen_ids:
                    continue
                # Only include online resources
                if not getattr(resource, "presence", False):
                    continue

                seen_ids.add(client_id)
                product = getattr(resource, "product", "Unknown")
                platform = getattr(resource, "platform", "Unknown") or getattr(resource, "platformVersion", "Unknown")
                result.append(PlexClientInfo(
                    client_id=client_id,
                    name=resource.name,
                    product=product,
                    platform=platform,
                    is_playing=client_id in playing_ids,
                    is_mobile=self._is_mobile_client(product, platform),
                ))
        except Exception as e:
            logger.warning("Failed to query account resources for players: %s", e)

        logger.debug("Client discovery: %d found (%d local, %d cloud)", len(result), local_count, len(result) - local_count)
        return result

    def get_playlists(self) -> list[PlexPlaylistInfo]:
        """Get audio playlists from the Plex server.

        Returns:
            List of PlexPlaylistInfo sorted alphabetically by title.
        """
        if not self._server:
            return []

        try:
            playlists = self._server.playlists(playlistType="audio")
            result = [
                PlexPlaylistInfo(
                    rating_key=str(playlist.ratingKey),
                    title=playlist.title,
                    track_count=playlist.leafCount,
                )
                for playlist in playlists
                if not playlist.smart and not playlist.radio
            ]
            return sorted(result, key=lambda p: p.title.lower())
        except Exception as e:
            logger.exception("Failed to get playlists: %s", e)
            return []

    def update_playlist(
        self,
        playlist_id: str,
        rating_keys: list[str],
        mode: str = "replace",
        description: str = "",
    ) -> dict[str, Any]:
        """Update an existing Plex playlist by replacing or appending tracks.

        Handles the __scratch__ sentinel for auto-creating "MediaSage - Now Playing".

        Args:
            playlist_id: Rating key of target playlist, or "__scratch__" sentinel
            rating_keys: List of track rating keys to add
            mode: 'replace' or 'append'
            description: Optional playlist description/summary

        Returns:
            Dict with success, tracks_added, tracks_skipped, duplicates_skipped,
            playlist_url, error
        """
        if not self._server:
            return {"success": False, "error": "Not connected to Plex"}

        try:
            # Handle __scratch__ sentinel — find or create "MediaSage - Now Playing"
            if playlist_id == "__scratch__":
                with self._scratch_lock:
                    scratch_playlist = None
                    try:
                        for p in self._server.playlists(playlistType="audio"):
                            if p.title == "MediaSage - Now Playing":
                                scratch_playlist = p
                                break
                    except Exception as e:
                        logger.warning("Failed to search for scratch playlist: %s", e)

                    if scratch_playlist is None:
                        # Create new scratch playlist with the provided tracks
                        items = []
                        tracks_skipped = 0
                        for key in rating_keys:
                            try:
                                items.append(self._server.fetchItem(int(key)))
                            except Exception:
                                tracks_skipped += 1

                        if not items:
                            return {"success": False, "error": "No valid tracks found"}

                        playlist = self._server.createPlaylist(
                            "MediaSage - Now Playing", items=items
                        )

                        if description:
                            try:
                                playlist.edit(summary=description)
                            except Exception as e:
                                logger.warning("Failed to set playlist description: %s", e)

                        playlist_url = self._build_playlist_url(playlist.ratingKey)
                        return {
                            "success": True,
                            "tracks_added": len(items),
                            "tracks_skipped": tracks_skipped,
                            "duplicates_skipped": 0,
                            "playlist_url": playlist_url,
                        }
                    else:
                        # Use the existing scratch playlist
                        playlist_id = str(scratch_playlist.ratingKey)

            # Fetch the target playlist
            playlist = self._server.fetchItem(int(playlist_id))

            # Fetch new track objects
            items = []
            tracks_skipped = 0
            duplicates_skipped = 0

            if mode == "replace":
                # Fetch new items FIRST to avoid data loss if add fails
                for key in rating_keys:
                    try:
                        items.append(self._server.fetchItem(int(key)))
                    except Exception:
                        tracks_skipped += 1

                if not items:
                    return {
                        "success": False,
                        "error": "No valid tracks found to replace with",
                        "tracks_added": 0,
                        "tracks_skipped": tracks_skipped,
                        "duplicates_skipped": 0,
                    }

                # Add new items FIRST, then remove old ones.
                # This avoids data loss: if addItems fails, old tracks remain.
                # If removeItems fails, user has duplicates (recoverable) not emptiness.
                existing_items = playlist.items()
                playlist.addItems(items)
                warning = None
                if existing_items:
                    try:
                        playlist.removeItems(existing_items)
                    except Exception as e:
                        logger.warning("Failed to remove old items during replace: %s", e)
                        warning = "Replaced tracks were added but old tracks could not be removed. Playlist may contain duplicates."

            elif mode == "append":
                warning = None
                # Build set of existing rating keys for deduplication
                existing_keys = {
                    str(item.ratingKey) for item in playlist.items()
                }

                for key in rating_keys:
                    if key in existing_keys:
                        duplicates_skipped += 1
                        continue
                    try:
                        items.append(self._server.fetchItem(int(key)))
                    except Exception:
                        tracks_skipped += 1

                if items:
                    playlist.addItems(items)

            else:
                return {"success": False, "error": f"Unknown update mode: {mode}"}

            # Update description if provided
            if description:
                try:
                    playlist.edit(summary=description)
                except Exception as e:
                    logger.warning("Failed to set playlist description: %s", e)

            playlist_url = self._build_playlist_url(playlist.ratingKey)
            result = {
                "success": True,
                "tracks_added": len(items),
                "tracks_skipped": tracks_skipped,
                "duplicates_skipped": duplicates_skipped,
                "playlist_url": playlist_url,
            }
            if warning:
                result["warning"] = warning
            return result
        except Exception as e:
            logger.exception("Failed to update playlist '%s'", playlist_id)
            return {"success": False, "error": str(e)}

    def _build_playlist_url(self, rating_key: int) -> str | None:
        """Build the Plex web app URL for a playlist."""
        machine_id = self.get_machine_identifier()
        if not machine_id:
            return None
        return (
            f"{self.url}/web/index.html#!/server/{machine_id}"
            f"/playlist?key=%2Fplaylists%2F{rating_key}"
        )

    def play_queue(
        self, rating_keys: list[str], client_id: str, mode: str = "replace"
    ) -> dict[str, Any]:
        """Create a play queue and start playback on a Plex client.

        Args:
            rating_keys: List of track rating keys
            client_id: machineIdentifier of target client
            mode: 'replace' (new queue) or 'play_next' (add after current)

        Returns:
            Dict with success, client_name, client_product, tracks_queued, error
        """
        if not self._server:
            return {"success": False, "error": "Not connected to Plex"}

        # Find the target client — try local GDM first, then cloud resources
        target_client = None
        try:
            for c in self._server.clients():
                if c.machineIdentifier == client_id:
                    target_client = c
                    break
        except Exception:
            pass

        # Fall back to cloud-connected resource
        if not target_client:
            try:
                account = self._server.myPlexAccount()
                for resource in account.resources():
                    if resource.clientIdentifier == client_id:
                        target_client = resource.connect()
                        break
            except Exception as e:
                logger.warning("Failed to connect to cloud client %s: %s", client_id, e)

        if not target_client:
            return {
                "success": False,
                "error": "Device couldn't be reached. Try starting playback on the device first, then re-open the picker.",
                "error_code": "not_found",
            }

        # Proxy through server for reliable communication
        try:
            target_client.proxyThroughServer(value=True)
        except Exception:
            pass  # Best effort — some clients don't support this

        # Fetch track objects
        tracks = []
        tracks_skipped = 0
        for key in rating_keys:
            try:
                item = self._server.fetchItem(int(key))
                tracks.append(item)
            except Exception as e:
                tracks_skipped += 1
                logger.warning("Failed to fetch track %s for play queue: %s", key, e)

        if not tracks:
            return {"success": False, "error": "No valid tracks found"}

        try:
            if mode == "replace":
                play_queue = PlayQueue.create(
                    self._server,
                    items=tracks,
                    startItem=tracks[0],
                    includeRelated=0,
                )
                target_client.playMedia(play_queue)
            elif mode == "play_next":
                # Get current play queue from client timeline entries
                # timelines() returns a list; timeline is a single object/None
                play_queue_id = None
                try:
                    for entry in target_client.timelines():
                        if entry.type == "music" and getattr(entry, "playQueueID", None):
                            play_queue_id = entry.playQueueID
                            break
                except Exception as e:
                    logger.warning("Failed to get timelines from client %s: %s", client_id, e)
                    return {"success": False, "error": "Could not read active queue from client"}
                if not play_queue_id:
                    return {
                        "success": False,
                        "error": "No active play queue on this client",
                    }
                existing_queue = PlayQueue.get(
                    self._server, play_queue_id, own=True
                )
                # Add in reverse order so tracks play in intended order
                tracks_queued = 0
                reversed_tracks = list(reversed(tracks))
                for i, track in enumerate(reversed_tracks):
                    try:
                        is_last = i == len(reversed_tracks) - 1
                        existing_queue.addItem(track, playNext=True, refresh=is_last)
                        tracks_queued += 1
                    except Exception:
                        logger.warning("Failed to add track %s to queue", track.ratingKey)

            else:
                return {"success": False, "error": f"Unknown play queue mode: {mode}"}

            actual_queued = tracks_queued if mode == "play_next" else len(tracks)
            return {
                "success": True,
                "client_name": target_client.title,
                "client_product": target_client.product,
                "tracks_queued": actual_queued,
                "tracks_skipped": tracks_skipped,
            }
        except ConnectionError:
            return {
                "success": False,
                "error": f"Client '{target_client.title}' went offline during playback",
            }
        except Exception as e:
            logger.exception("Failed to create play queue on '%s'", target_client.title)
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
