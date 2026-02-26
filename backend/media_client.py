"""Abstract base class and shared utilities for media server clients."""

import re
from abc import ABC, abstractmethod
from typing import Any

from unidecode import unidecode

from backend.models import Track

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


def is_live_track(title: str, album_title: str) -> bool:
    """Check if a track appears to be a live recording based on title strings.

    Args:
        title: Track title
        album_title: Album title

    Returns:
        True if track appears to be a live version
    """
    for text in [album_title, title]:
        if re.search(DATE_PATTERN, text):
            return True
        if re.search(LIVE_KEYWORDS, text, re.IGNORECASE):
            return True
    return False


class BaseMediaClient(ABC):
    """Abstract base class for media server clients (Plex, Jellyfin, etc.)."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to the media server."""
        ...

    @abstractmethod
    def get_error(self) -> str | None:
        """Get the last error message, if any."""
        ...

    @abstractmethod
    def get_music_libraries(self) -> list[str]:
        """Get list of music library names."""
        ...

    @abstractmethod
    def get_library_stats(self) -> dict[str, Any]:
        """Get statistics about the music library.

        Returns:
            Dict with total_tracks, genres, and decades
        """
        ...

    @abstractmethod
    def get_all_tracks(self) -> list[Track]:
        """Get all tracks from the library as Track models."""
        ...

    @abstractmethod
    def get_all_albums_metadata(self) -> dict[str, dict[str, Any]]:
        """Fetch all albums and return mapping of rating_key -> metadata.

        Returns:
            Dict mapping album rating_key (as string) to dict with 'genres' and 'year'
        """
        ...

    @abstractmethod
    def get_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
        limit: int = 0,
    ) -> list[Track]:
        """Get tracks matching filter criteria."""
        ...

    @abstractmethod
    def get_random_tracks(
        self,
        count: int,
        exclude_live: bool = True,
    ) -> list[Track]:
        """Get random tracks from the library."""
        ...

    @abstractmethod
    def get_track_by_key(self, rating_key: str) -> Track | None:
        """Get a single track by its rating key."""
        ...

    @abstractmethod
    def search_tracks(self, query: str) -> list[Track]:
        """Search for tracks by title or artist."""
        ...

    @abstractmethod
    def count_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
    ) -> int:
        """Count tracks matching filter criteria (fast, no full fetch)."""
        ...

    @abstractmethod
    def create_playlist(
        self,
        name: str,
        rating_keys: list[str],
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new playlist.

        Returns:
            Dict with success, playlist_id, playlist_url, tracks_added, tracks_skipped
        """
        ...

    @abstractmethod
    def update_playlist(
        self,
        playlist_id: str,
        rating_keys: list[str],
        mode: str = "replace",
        description: str = "",
    ) -> dict[str, Any]:
        """Update an existing playlist by replacing or appending tracks."""
        ...

    @abstractmethod
    def get_playlists(self) -> list:
        """Get all audio playlists from the media server."""
        ...

    @abstractmethod
    def get_art_url(self, item_id: str) -> str | None:
        """Get the art URL for an item (for backend proxying).

        Returns:
            Full URL string for art, or None if not available
        """
        ...
