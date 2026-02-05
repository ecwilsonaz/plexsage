"""Plex server client for library queries and playlist management."""

import logging
import re
from typing import Any

from plexapi.server import PlexServer

logger = logging.getLogger(__name__)
from plexapi.exceptions import NotFound, Unauthorized
from rapidfuzz import fuzz
from requests.exceptions import ConnectionError, Timeout
from unidecode import unidecode

from backend.models import Track


class PlexQueryError(Exception):
    """Raised when a Plex library query fails."""

    pass


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
        track: Plex track object

    Returns:
        True if track appears to be a live version
    """
    album_title = ""
    if callable(getattr(track, 'album', None)):
        album = track.album()
        album_title = album.title if album else ""
    elif hasattr(track, 'parentTitle'):
        album_title = track.parentTitle or ""

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
    ) -> list[Track]:
        """Get tracks matching filter criteria.

        Args:
            genres: List of genre names to include
            decades: List of decades (e.g., ["1990s", "2000s"])
            exclude_live: Whether to exclude live recordings
            min_rating: Minimum user rating (0-10, 0 = no filter)

        Returns:
            List of matching Track objects
        """
        if not self._library:
            return []

        try:
            # Build Plex filter kwargs for efficient server-side filtering
            filters = {}

            if genres:
                filters['genre'] = genres

            if decades:
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

            # Fetch tracks with server-side filters
            plex_tracks = self._library.search(libtype="track", **filters)

            # Post-filter for live versions (can't be done server-side)
            if exclude_live:
                plex_tracks = [t for t in plex_tracks if not is_live_version(t)]

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
        """Get count of tracks matching filter criteria.

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
            # Build Plex filter kwargs
            filters = {}

            if genres:
                # Plex uses 'genre' filter with genre names
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
                # Filter for tracks with rating >= min_rating
                # Plex uses '>>=' for greater-than-or-equal
                filters['userRating>>='] = min_rating

            # If no filters, return total track count (fast path)
            if not filters:
                return self._library.totalViewSize(libtype="track")

            # Search and count results
            # Note: This fetches metadata for all matching tracks but doesn't
            # convert them, so it's reasonably fast for count purposes
            results = self._library.search(libtype="track", **filters)
            return len(results)
        except Exception:
            return -1

    def get_genres(self) -> list[dict[str, Any]]:
        """Get list of genres with track counts."""
        stats = self.get_library_stats()
        return stats.get("genres", [])

    def get_decades(self) -> list[dict[str, Any]]:
        """Get list of decades with track counts."""
        stats = self.get_library_stats()
        return stats.get("decades", [])

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
            return {
                "success": True,
                "playlist_id": str(playlist.ratingKey),
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
